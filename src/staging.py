"""
Load a flat file into a staging table.

The Flat Files page reads files without touching the database. This module
is the one deliberate exception: it takes a file's rows and inserts them
into STG_CUSTOMER / STG_POLICY / STG_CLAIM under a batch id, so the rest of
the pipeline (transformation, curated, the saved test cases) runs on data
someone brought in themselves.

Checks are split in two:

  * BLOCKING — things the database would reject anyway: a required column
    missing from the file, an empty business key, text in a numeric column,
    a date that isn't a date. Nothing is written while any of these exist.
  * WARNINGS — data-quality problems such as duplicate keys. Those load, on
    purpose: catching them is the job of the saved test cases.

A load either writes every row or none: the old rows for the batch are
removed and the new ones inserted inside one transaction.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

# Staging table -> (the file the demo pipeline reads, columns in load order).
# tools/generate_stg_load_sql.py reads this too, so there is one definition.
STAGING_TABLES: dict[str, tuple[str, list[str]]] = {
    "STG_CUSTOMER": ("customers.csv",
                     ["CUSTOMER_ID", "CUSTOMER_NAME", "EMAIL", "PHONE_NUMBER", "CITY",
                      "LAST_UPDATED_TS"]),
    "STG_POLICY": ("policies.csv",
                   ["POLICY_ID", "CUSTOMER_ID", "POLICY_NUMBER", "POLICY_TYPE", "POLICY_STATUS",
                    "PREMIUM_AMOUNT", "SUM_INSURED", "ISSUE_DATE", "EXPIRY_DATE",
                    "LAST_UPDATED_TS"]),
    "STG_CLAIM": ("claims.csv",
                  ["CLAIM_ID", "POLICY_ID", "CUSTOMER_ID", "CLAIM_DATE", "CLAIM_AMOUNT",
                   "APPROVED_AMOUNT", "CLAIM_STATUS", "INCIDENT_TYPE", "LAST_UPDATED_TS"]),
}

# NOT NULL in sql/01_ddl_create_all_tables.sql
REQUIRED = {
    "STG_CUSTOMER": ["CUSTOMER_ID"],
    "STG_POLICY": ["POLICY_ID", "CUSTOMER_ID"],
    "STG_CLAIM": ["CLAIM_ID", "POLICY_ID", "CUSTOMER_ID"],
}
BUSINESS_KEY = {"STG_CUSTOMER": "CUSTOMER_ID", "STG_POLICY": "POLICY_ID", "STG_CLAIM": "CLAIM_ID"}
NUMERIC = {"PREMIUM_AMOUNT", "SUM_INSURED", "CLAIM_AMOUNT", "APPROVED_AMOUNT"}
DATES = {"ISSUE_DATE", "EXPIRY_DATE", "CLAIM_DATE"}
TIMESTAMPS = {"LAST_UPDATED_TS"}
MAX_LENGTH = {  # VARCHAR sizes from the DDL; longer values would be rejected
    "CUSTOMER_ID": 20, "POLICY_ID": 20, "CLAIM_ID": 20, "CUSTOMER_NAME": 200, "EMAIL": 200,
    "PHONE_NUMBER": 20, "CITY": 100, "POLICY_NUMBER": 30, "POLICY_TYPE": 20,
    "POLICY_STATUS": 20, "CLAIM_STATUS": 20, "INCIDENT_TYPE": 100,
}

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?")
MAX_EXAMPLES = 5
# Keep rows x columns per INSERT well under MSSQL's 2,100-parameter ceiling.
_MAX_PARAMS_PER_INSERT = 2000


@dataclass
class StagingCheck:
    table: str
    row_count: int = 0
    missing_columns: list[str] = field(default_factory=list)
    extra_columns: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)     # blocking
    warnings: list[str] = field(default_factory=list)   # load anyway

    @property
    def ok(self) -> bool:
        return not self.missing_columns and not self.errors


def guess_staging_table(name: str) -> str | None:
    """CLAIMS / claim_2026 -> STG_CLAIM, policies -> STG_POLICY, customer -> STG_CUSTOMER."""
    upper = name.upper()
    for table, stem in (("STG_CLAIM", "CLAIM"), ("STG_POLICY", "POLIC"),
                        ("STG_CUSTOMER", "CUSTOMER")):
        if stem in upper:
            return table
    return None


def check_for_staging(table: str, columns: list[str], rows: list[tuple]) -> StagingCheck:
    """Everything that would stop, or should be known before, loading `rows` into `table`."""
    if table not in STAGING_TABLES:
        raise ValueError(f"'{table}' is not a staging table. "
                         f"Choose one of: {', '.join(STAGING_TABLES)}")
    expected = STAGING_TABLES[table][1]
    upper = [c.strip().upper() for c in columns]
    check = StagingCheck(
        table=table, row_count=len(rows),
        missing_columns=[c for c in expected if c not in upper],
        extra_columns=[c for c, u in zip(columns, upper) if u not in expected and u != "BATCH_ID"],
    )
    if check.missing_columns:
        return check
    if not rows:
        check.errors.append("The file has no rows.")
        return check

    idx = {c: upper.index(c) for c in expected}

    def report(problem: str, bad: list[str]) -> None:
        if bad:
            shown = ", ".join(bad[:MAX_EXAMPLES]) + (" …" if len(bad) > MAX_EXAMPLES else "")
            check.errors.append(f"{problem} in {len(bad)} row(s): {shown}")

    for col in REQUIRED[table]:
        report(f"{col} is empty",
               [f"row {n}" for n, r in enumerate(rows, 1) if _blank(r[idx[col]])])
    for col in expected:
        i = idx[col]
        if col in NUMERIC:
            report(f"{col} is not a number",
                   [f"row {n} ({r[i]!r})" for n, r in enumerate(rows, 1)
                    if not _blank(r[i]) and not _is_number(r[i])])
        elif col in DATES:
            report(f"{col} is not a YYYY-MM-DD date",
                   [f"row {n} ({r[i]!r})" for n, r in enumerate(rows, 1)
                    if not _blank(r[i]) and not _is_date(r[i], _DATE_RE)])
        elif col in TIMESTAMPS:
            report(f"{col} is not a YYYY-MM-DD HH:MM:SS timestamp",
                   [f"row {n} ({r[i]!r})" for n, r in enumerate(rows, 1)
                    if not _blank(r[i]) and not _is_date(r[i], _TS_RE)])
        elif col in MAX_LENGTH:
            report(f"{col} is longer than {MAX_LENGTH[col]} characters",
                   [f"row {n}" for n, r in enumerate(rows, 1)
                    if not _blank(r[i]) and len(_text(r[i])) > MAX_LENGTH[col]])

    key_i = idx[BUSINESS_KEY[table]]
    seen, dupes = set(), set()
    for r in rows:
        k = _text(r[key_i])
        (dupes if k in seen else seen).add(k)
    if dupes:
        check.warnings.append(
            f"{len(dupes)} duplicate {BUSINESS_KEY[table]} value(s), e.g. "
            f"{', '.join(sorted(dupes)[:MAX_EXAMPLES])}. They will load — the "
            "duplicate-check test cases are there to flag them.")
    if check.extra_columns:
        check.warnings.append(f"Ignored column(s) not in {table}: "
                              f"{', '.join(check.extra_columns)}")
    return check


def read_file_as_text(path: str, options: dict | None = None):
    """
    (columns, rows) for one file, every value as text — the flat-file
    connection guesses types, which would turn +917107420369 into a number.
    Only for files that hold a single table (not multi-sheet workbooks).
    """
    from src.connectors.base import ConnectionProfile
    from src.connectors.flatfile_connector import FlatFileConnector

    opts = {**(options or {}), "path": path, "all_text": True}
    with FlatFileConnector(ConnectionProfile("flatfile", "flatfile", opts)) as ff:
        tables = ff.describe()
        if ff.load_errors:
            raise ValueError(ff.load_errors[0]["error"])
        if len(tables) != 1:
            raise ValueError(f"{path} holds {len(tables)} tables; load one sheet at a time.")
        res = ff.query(f'SELECT * FROM "{tables[0]["table"]}"')
        return res.columns, res.rows


def load_to_staging(connector, table: str, columns: list[str], rows: list[tuple],
                    batch_id: str, source_file: str = "", replace: bool = True) -> dict:
    """
    Insert `rows` into `table` for `batch_id`, all or nothing.

    replace=True first removes the rows already staged for this batch in
    this table, so loading the same file twice doesn't double it up.
    Returns {"table", "batch_id", "deleted", "inserted", "staged_now"}.
    """
    check = check_for_staging(table, columns, rows)
    if not check.ok:
        problems = ([f"missing column(s): {', '.join(check.missing_columns)}"]
                    if check.missing_columns else []) + check.errors
        raise ValueError(f"Not loaded into {table}: " + "; ".join(problems))

    expected = STAGING_TABLES[table][1]
    upper = [c.strip().upper() for c in columns]
    order = [upper.index(c) for c in expected]
    values = [[_clean(c, r[i]) for c, i in zip(expected, order)] for r in rows]

    per_insert = max(1, _MAX_PARAMS_PER_INSERT // (len(expected) + 1))
    col_list = ", ".join(expected + ["BATCH_ID"])

    if getattr(connector, "kind", "") == "snowflake":
        connector.execute("BEGIN")  # the Snowflake driver autocommits otherwise
    try:
        deleted = 0
        if replace:
            deleted = connector.execute(f"DELETE FROM {table} WHERE BATCH_ID = :batch_id",
                                        {"batch_id": batch_id})
        for start in range(0, len(values), per_insert):
            chunk = values[start:start + per_insert]
            params, groups = {"batch_id": batch_id}, []
            for n, row in enumerate(chunk):
                names = [f"r{n}_{j}" for j in range(len(expected))]
                params.update(zip(names, row))
                groups.append("(" + ", ".join(f":{p}" for p in names) + ", :batch_id)")
            connector.execute(f"INSERT INTO {table} ({col_list}) VALUES " + ", ".join(groups),
                              params)
        _record_batch(connector, batch_id, source_file)
        connector.commit()
    except Exception:
        connector.rollback()
        raise

    staged = connector.query(f"SELECT COUNT(*) FROM {table} WHERE BATCH_ID = :batch_id",
                             {"batch_id": batch_id}).rows[0][0]
    return {"table": table, "batch_id": batch_id, "deleted": max(deleted or 0, 0),
            "inserted": len(values), "staged_now": int(staged)}


def _record_batch(connector, batch_id: str, source_file: str) -> None:
    """
    Keep DQ_BATCH_METADATA in step with what is staged for the batch — the
    'File-to-stage row count reconciliation' test compares against it.
    """
    total = 0
    for table in STAGING_TABLES:
        total += int(connector.query(f"SELECT COUNT(*) FROM {table} WHERE BATCH_ID = :b",
                                     {"b": batch_id}).rows[0][0])
    previous = connector.query("SELECT SOURCE_FILE FROM DQ_BATCH_METADATA WHERE BATCH_ID = :b",
                               {"b": batch_id}).rows
    files = [f for f in ((previous[0][0] or "").split(", ") if previous else []) if f]
    if source_file and source_file not in files:
        files.append(source_file)
    connector.execute("DELETE FROM DQ_BATCH_METADATA WHERE BATCH_ID = :b", {"b": batch_id})
    connector.execute(
        "INSERT INTO DQ_BATCH_METADATA (BATCH_ID, SOURCE_SYSTEM, SOURCE_FILE, SOURCE_ROWS, "
        "LOADED_ROWS, REJECT_ROWS, LOAD_STATUS) "
        "VALUES (:b, 'FLAT_FILE_UPLOAD', :f, :n, :n, 0, 'LOADED')",
        {"b": batch_id, "f": ", ".join(files)[:500], "n": total})


# ---------------------------------------------------------------------------
def _text(v) -> str:
    return "" if v is None else str(v).strip()


def _blank(v) -> bool:
    return v is None or (isinstance(v, float) and v != v) or _text(v) == ""


def _is_number(v) -> bool:
    if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool):
        return v == v  # NaN is not a number
    try:
        Decimal(_text(v))
        return True
    except InvalidOperation:
        return False


def _is_date(v, pattern) -> bool:
    if isinstance(v, (date, datetime)):
        return True
    s = _text(v)
    if not pattern.fullmatch(s):
        return False
    try:
        (date.fromisoformat if len(s) == 10 else datetime.fromisoformat)(s)
        return True
    except ValueError:
        return False


def _clean(column: str, v):
    """
    The value to bind: NULL for blanks, plain digits for amounts, text for
    everything else. Text binds the same way on every driver, and each
    database casts it to the column's NUMBER / DATE / TIMESTAMP type.
    """
    if _blank(v):
        return None
    if column in NUMERIC:
        return format(Decimal(_text(v)), "f")
    if isinstance(v, datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))  # an all-digit ID read as a number arrives as 1001.0
    return str(v)
