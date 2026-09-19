"""
Source-to-target row comparison.

Pulls the source result set and the target result set — possibly from two
DIFFERENT databases (Snowflake vs MSSQL) — matches them on a business key
and reports, per record and per column, exactly what differs.

This is what produces the "failed records with mismatch details" the brief
asks for: expected value, actual value, and why it failed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from src.history import FailureRecord

MISSING_IN_TARGET = "MISSING_IN_TARGET"
EXTRA_IN_TARGET = "EXTRA_IN_TARGET"
VALUE_MISMATCH = "VALUE_MISMATCH"
DUPLICATE_KEY = "DUPLICATE_KEY"
ROW_COUNT_MISMATCH = "ROW_COUNT_MISMATCH"


@dataclass
class ComparisonReport:
    source_row_count: int = 0
    target_row_count: int = 0
    matched_record_count: int = 0
    failures: list[FailureRecord] = None
    compared_columns: list[str] = None

    def __post_init__(self):
        self.failures = self.failures if self.failures is not None else []
        self.compared_columns = self.compared_columns if self.compared_columns is not None else []

    @property
    def failed_record_count(self) -> int:
        return len(self.failures)

    @property
    def status(self) -> str:
        return "PASS" if not self.failures else "FAIL"


def normalise(value, tolerance: float = 0.0):
    """
    Reduce a driver-specific value to something comparable across databases.

    Snowflake hands back Decimal where MSSQL may hand back float; dates may
    arrive as date or datetime; VARCHAR padding differs. Without this, a
    cross-database comparison reports mismatches that aren't real.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        d = Decimal(str(value))
        return d.normalize() if tolerance == 0 else d
    if isinstance(value, datetime):
        return value.replace(microsecond=0)
    if isinstance(value, date):
        return value
    if isinstance(value, bytes):
        return value
    s = str(value).strip()
    # A numeric string and the number it spells are the same value.
    try:
        return Decimal(s).normalize()
    except (InvalidOperation, ValueError):
        return s


def values_equal(a, b, tolerance: float = 0.0) -> bool:
    na, nb = normalise(a, tolerance), normalise(b, tolerance)
    if na is None or nb is None:
        return na is nb or (na is None and nb is None)
    if isinstance(na, Decimal) and isinstance(nb, Decimal):
        if tolerance:
            return abs(na - nb) <= Decimal(str(tolerance))
        return na == nb
    if isinstance(na, Decimal) != isinstance(nb, Decimal):
        return str(na) == str(nb)
    return na == nb


def _fmt(value) -> str:
    """Render a value for the failure log — plain digits, never 1.2E+4."""
    if value is None:
        return "<NULL>"
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, float):
        return format(Decimal(str(value)).normalize(), "f")
    return str(value)


def _key_of(row: dict, key_columns: list[str]) -> str:
    return " | ".join(_fmt(row.get(k)) for k in key_columns)


def compare_result_sets(
    source_columns: list[str],
    source_rows: list[tuple],
    target_columns: list[str],
    target_rows: list[tuple],
    key_columns: list[str],
    compare_columns: list[str] | None = None,
    tolerance: float = 0.0,
    max_failures: int = 1000,
) -> ComparisonReport:
    """Match source rows to target rows on the business key and diff them."""
    src_cols_u = [c.upper() for c in source_columns]
    tgt_cols_u = [c.upper() for c in target_columns]
    keys = [k.upper() for k in key_columns]

    missing_src = [k for k in keys if k not in src_cols_u]
    missing_tgt = [k for k in keys if k not in tgt_cols_u]
    if missing_src or missing_tgt:
        where = []
        if missing_src:
            where.append(f"source is missing {', '.join(missing_src)}")
        if missing_tgt:
            where.append(f"target is missing {', '.join(missing_tgt)}")
        raise ValueError(
            "Key column(s) not present in the result set: " + "; ".join(where) +
            f". Source returns [{', '.join(src_cols_u)}], target returns [{', '.join(tgt_cols_u)}]."
        )

    src = [dict(zip(src_cols_u, r)) for r in source_rows]
    tgt = [dict(zip(tgt_cols_u, r)) for r in target_rows]

    # Columns to diff: explicit list, else every non-key column both sides share.
    if compare_columns:
        cols = [c.upper() for c in compare_columns]
        unknown = [c for c in cols if c not in src_cols_u or c not in tgt_cols_u]
        if unknown:
            raise ValueError(
                f"Compare column(s) {', '.join(unknown)} are not returned by both queries."
            )
    else:
        cols = [c for c in src_cols_u if c in tgt_cols_u and c not in keys]

    report = ComparisonReport(
        source_row_count=len(src), target_row_count=len(tgt), compared_columns=cols
    )

    src_index, src_dupes = _index_by_key(src, keys)
    tgt_index, tgt_dupes = _index_by_key(tgt, keys)

    for key, n in sorted(src_dupes.items()):
        report.failures.append(FailureRecord(
            business_key=key, column_name=",".join(keys),
            expected_value="1 row", actual_value=f"{n} rows",
            failure_type=DUPLICATE_KEY,
            failure_reason=f"Business key appears {n} times in the SOURCE result set",
        ))
    for key, n in sorted(tgt_dupes.items()):
        report.failures.append(FailureRecord(
            business_key=key, column_name=",".join(keys),
            expected_value="1 row", actual_value=f"{n} rows",
            failure_type=DUPLICATE_KEY,
            failure_reason=f"Business key appears {n} times in the TARGET result set",
        ))

    for key in src_index.keys() - tgt_index.keys():
        report.failures.append(FailureRecord(
            business_key=key, column_name=",".join(keys),
            expected_value="record present in target", actual_value="<NOT FOUND>",
            failure_type=MISSING_IN_TARGET,
            failure_reason="Record exists in source but was not loaded into target",
        ))

    for key in tgt_index.keys() - src_index.keys():
        report.failures.append(FailureRecord(
            business_key=key, column_name=",".join(keys),
            expected_value="<NOT IN SOURCE>", actual_value="record present in target",
            failure_type=EXTRA_IN_TARGET,
            failure_reason="Record exists in target but has no matching source record",
        ))

    for key in sorted(src_index.keys() & tgt_index.keys()):
        s_row, t_row = src_index[key], tgt_index[key]
        row_ok = True
        for col in cols:
            if not values_equal(s_row.get(col), t_row.get(col), tolerance):
                row_ok = False
                report.failures.append(FailureRecord(
                    business_key=key, column_name=col,
                    expected_value=_fmt(s_row.get(col)), actual_value=_fmt(t_row.get(col)),
                    failure_type=VALUE_MISMATCH,
                    failure_reason=f"{col} does not match between source and target",
                ))
                if len(report.failures) >= max_failures:
                    break
        if row_ok:
            report.matched_record_count += 1
        if len(report.failures) >= max_failures:
            report.failures.append(FailureRecord(
                business_key="", column_name="",
                expected_value="", actual_value="",
                failure_type="TRUNCATED",
                failure_reason=f"Stopped after {max_failures} mismatches — narrow the test's SQL",
            ))
            break

    return report


def compare_row_counts(source_count: int, target_count: int, tolerance: float = 0.0
                       ) -> ComparisonReport:
    report = ComparisonReport(source_row_count=source_count, target_row_count=target_count)
    if abs(source_count - target_count) > tolerance:
        report.failures.append(FailureRecord(
            business_key="<row count>", column_name="ROW_COUNT",
            expected_value=str(source_count), actual_value=str(target_count),
            failure_type=ROW_COUNT_MISMATCH,
            failure_reason=(f"Source returned {source_count} row(s), "
                            f"target returned {target_count}"),
        ))
    else:
        report.matched_record_count = min(source_count, target_count)
    return report


def _index_by_key(rows: list[dict], keys: list[str]) -> tuple[dict, dict]:
    index, counts = {}, {}
    for row in rows:
        k = _key_of(row, keys)
        counts[k] = counts.get(k, 0) + 1
        index.setdefault(k, row)
    return index, {k: n for k, n in counts.items() if n > 1}
