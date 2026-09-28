"""
Promote a staged batch to the curated layer, then validate it.

After a flat file is loaded into staging (src/staging.py), this runs the
same two steps the demo pipeline does, for that batch only:

  03_transform_load.sql     staging -> transformation, applying the mapping
                            rules CUS-*, POL-*, CLM-*
  04_curated_merge_load.sql transformation -> curated (MERGE on the business
                            key) and the incremental watermarks

Both scripts run in one transaction, so a failure leaves curated as it was.
Then the saved test cases in /Source-to-Target, /Historical and
/Incremental are run against the batch, with the historical date window set
to the dates the batch covers.

Checks before promoting are split like the staging ones:

  * BLOCKING — nothing staged for the batch, or a business key that appears
    more than once in it. Curated holds one row per key, and there is no
    way to tell which of the duplicates is right.
  * WARNINGS — claims whose policy is neither in the batch nor already in
    curated. Rule CLM-006 drops them; the rest of the batch still promotes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from src import etl_loader
from src.connectors.base import split_statements
from src.staging import BUSINESS_KEY, MAX_EXAMPLES, STAGING_TABLES

# Staging table -> the transformation and curated tables it feeds.
LAYERS = {
    "STG_CUSTOMER": ("TRN_CUSTOMER", "CUSTOMER_360"),
    "STG_POLICY": ("TRN_POLICY", "POLICY_MASTER"),
    "STG_CLAIM": ("TRN_CLAIM", "CLAIM_MASTER"),
}
VALIDATION_FOLDERS = ("/Source-to-Target", "/Historical", "/Incremental")
_TRANSFORM_SCRIPTS = ("03_transform_load.sql", "04_curated_merge_load.sql")


@dataclass
class PromotionCheck:
    batch_id: str
    staged: dict = field(default_factory=dict)          # STG table -> rows in the batch
    errors: list[str] = field(default_factory=list)     # blocking
    warnings: list[str] = field(default_factory=list)   # promote anyway
    dropped_claims: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def check_for_promotion(connector, batch_id: str) -> PromotionCheck:
    """Everything that would stop, or should be known before, promoting `batch_id`."""
    check = PromotionCheck(batch_id=batch_id)
    b = {"b": batch_id}
    for table in STAGING_TABLES:
        check.staged[table] = int(connector.query(
            f"SELECT COUNT(*) FROM {table} WHERE BATCH_ID = :b", b).rows[0][0])
    if not any(check.staged.values()):
        check.errors.append(f"Nothing is staged for batch {batch_id}. Load a file into "
                            "staging first.")
        return check

    for table, key in BUSINESS_KEY.items():
        if not check.staged[table]:
            continue
        dupes = [r[0] for r in connector.query(
            f"SELECT {key} FROM {table} WHERE BATCH_ID = :b "
            f"GROUP BY {key} HAVING COUNT(*) > 1 ORDER BY {key}", b).rows]
        if dupes:
            check.errors.append(
                f"{len(dupes)} {key} value(s) appear more than once in {table} for this "
                f"batch: {_examples(dupes)}. Curated keeps one row per {key}, so remove the "
                "duplicates from the file and load it again.")

    if check.staged["STG_CLAIM"]:
        check.dropped_claims = [r[0] for r in connector.query(
            "SELECT S.CLAIM_ID FROM STG_CLAIM S "
            "WHERE S.BATCH_ID = :b AND S.CLAIM_ID IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM STG_POLICY P "
            "                WHERE P.POLICY_ID = S.POLICY_ID AND P.BATCH_ID = S.BATCH_ID) "
            "AND NOT EXISTS (SELECT 1 FROM POLICY_MASTER M WHERE M.POLICY_ID = S.POLICY_ID) "
            "ORDER BY S.CLAIM_ID", b).rows]
        if check.dropped_claims:
            check.warnings.append(
                f"{len(check.dropped_claims)} claim(s) point at a policy that is neither in "
                f"this batch nor in POLICY_MASTER and will not reach curated (rule CLM-006): "
                f"{_examples(check.dropped_claims)}. Load the policies first — into this "
                "batch or an earlier one — to include them.")
    return check


def promote_to_curated(connector, batch_id: str) -> dict:
    """
    Run the transformation rules and the curated MERGE for `batch_id`, all
    or nothing. Returns {"batch_id", "staged", "transformed", "curated",
    "dropped_claims", "window"}; `curated` counts the batch's keys now
    present in each curated table.
    """
    check = check_for_promotion(connector, batch_id)
    if not check.ok:
        raise ValueError(f"Batch {batch_id} was not promoted: " + "; ".join(check.errors))

    params = {"batch_id": batch_id}
    if getattr(connector, "kind", "") == "snowflake":
        connector.execute("BEGIN")  # the Snowflake driver autocommits otherwise
    try:
        for script in _TRANSFORM_SCRIPTS:
            for stmt in split_statements(etl_loader.read_script(script)):
                connector.execute(stmt, params)
        connector.commit()
    except Exception:
        connector.rollback()
        raise

    b = {"b": batch_id}
    transformed, curated = {}, {}
    for stg, (trn, cur) in LAYERS.items():
        key = BUSINESS_KEY[stg]
        transformed[trn] = int(connector.query(
            f"SELECT COUNT(*) FROM {trn} WHERE BATCH_ID = :b", b).rows[0][0])
        curated[cur] = int(connector.query(
            f"SELECT COUNT(*) FROM {cur} WHERE {key} IN "
            f"(SELECT {key} FROM {trn} WHERE BATCH_ID = :b)", b).rows[0][0])
    return {"batch_id": batch_id, "staged": check.staged, "transformed": transformed,
            "curated": curated, "dropped_claims": check.dropped_claims,
            "window": validation_window(connector, batch_id)}


def validation_window(connector, batch_id: str) -> tuple[str, str] | None:
    """
    (history_start, history_end) as YYYY-MM-DD: the first and last day any
    record in the batch is effective. None when nothing was transformed.
    """
    lows, highs = [], []
    for trn, _ in LAYERS.values():
        low, high = connector.query(
            f"SELECT MIN(RECORD_EFFECTIVE_TS), MAX(RECORD_EFFECTIVE_TS) FROM {trn} "
            "WHERE BATCH_ID = :b", {"b": batch_id}).rows[0]
        if low is not None:
            lows.append(_as_date(low))
            highs.append(_as_date(high))
    if not lows:
        return None
    return min(lows).isoformat(), max(highs).isoformat()


def validate_curated(engine, project_id: str, batch_id: str,
                     window: tuple[str, str] | None = None, on_progress=None) -> dict:
    """
    Run /Source-to-Target, /Historical and /Incremental against the batch.

    `window` overrides the historical tests' saved date range; pass the one
    promote_to_curated returned so they look at the dates this batch covers.
    Returns {folder path: [ExecutionResult]} — a folder the project doesn't
    have maps to None.
    """
    params = {"history_start": window[0], "history_end": window[1]} if window else None
    out = {}
    for path in VALIDATION_FOLDERS:
        folder = engine.repo.find_folder_by_path(project_id, path)
        out[path] = None if folder is None else engine.run_folder(
            folder["FOLDER_ID"], params=params, batch_id=batch_id, on_progress=on_progress)
    return out


# ---------------------------------------------------------------------------
def _examples(values: list) -> str:
    return ", ".join(map(str, values[:MAX_EXAMPLES])) + (" …" if len(values) > MAX_EXAMPLES
                                                         else "")


def _as_date(v) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])
