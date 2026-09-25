"""
Validation engine.

Executes a SAVED test case (loaded from the repository at its latest
version) and records the outcome in the execution history. Five test types
are supported, each with its own notion of what "passing" means — the old
"any row returned means FAIL" rule is only correct for rule queries, and
applying it to a reconciliation report made those tests impossible to pass.

    SOURCE_TARGET_COMPARE  match source rows to target rows on a business
                           key; report per-column expected vs actual
    SQL_ROWCOUNT           a rule query; zero rows returned = PASS
    ROW_COUNT_MATCH        source count vs target count
    STATUS_COLUMN          query returns a STATUS column; any non-PASS fails
    INFORMATIONAL          always PASS; records the rows for reporting
"""
from __future__ import annotations

import time

from src.comparator import compare_result_sets, compare_row_counts
from src.config import DEFAULT_BATCH_ID, DEFAULT_CONNECTION, DEFAULT_USER, now_ist
from src.connectors import get_connector
from src.history import ExecutionResult, FailureRecord, HistoryStore, new_run_id
from src.repository import Repository, TestCase

# Result-set columns the engine recognises when turning rule-query rows
# into mismatch detail. Anything else falls back to "first column is the key".
KEY_ALIASES = ("BUSINESS_KEY", "KEY", "ID")
COLUMN_ALIASES = ("COLUMN_NAME", "COLUMN", "FIELD")
EXPECTED_ALIASES = ("EXPECTED_VALUE", "EXPECTED")
ACTUAL_ALIASES = ("ACTUAL_VALUE", "ACTUAL")
REASON_ALIASES = ("FAILURE_REASON", "REASON", "MESSAGE")
STATUS_ALIASES = ("STATUS", "RESULT", "VALIDATION_STATUS")

MAX_FAILURES = 1000


class DQEngine:
    """
    Runs saved test cases.

    Connectors are opened lazily and cached for the life of the engine, so
    a 40-test regression run makes one connection per distinct database
    rather than one per test.
    """

    def __init__(self, repository: Repository | None = None, executed_by: str = ""):
        self.repo = repository or Repository()
        self._own_repo = repository is None
        self.history = HistoryStore(self.repo.db)
        self.executed_by = executed_by or DEFAULT_USER
        self._connectors: dict[str, object] = {DEFAULT_CONNECTION: self.repo.db}

    # ------------------------------------------------------------- lifecycle
    def connector(self, name: str | None):
        name = (name or DEFAULT_CONNECTION).strip().lower()
        if name not in self._connectors:
            self._connectors[name] = get_connector(name)
        return self._connectors[name]

    def close(self):
        for name, conn in list(self._connectors.items()):
            if conn is not self.repo.db:
                try:
                    conn.close()
                except Exception:
                    pass
        self._connectors.clear()
        if self._own_repo:
            self.repo.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # ------------------------------------------------------------ public API
    def run_test_case(self, test_case: TestCase | str, params: dict | None = None,
                      batch_id: str = DEFAULT_BATCH_ID, run_id: str = "",
                      run_mode: str = "ADHOC", persist: bool = True) -> ExecutionResult:
        """Execute one saved test case and log the result."""
        tc = self.repo.get_test_case(test_case) if isinstance(test_case, str) else test_case
        folder_path = ""
        try:
            folder_path = self.repo.get_folder(tc.folder_id)["FOLDER_PATH"]
        except Exception:
            pass

        merged = self.resolve_params(tc, params, batch_id)
        result = ExecutionResult(
            run_id=run_id or new_run_id(),
            test_case_id=tc.test_case_id,
            test_name=tc.test_name,
            folder_path=folder_path,
            params=merged,
            test_version_no=tc.version_no,
        )

        started = time.perf_counter()
        try:
            self._dispatch(tc, merged, result)
        except Exception as exc:
            result.status = "ERROR"
            result.error_message = f"{type(exc).__name__}: {exc}"
        result.duration_ms = int((time.perf_counter() - started) * 1000)

        if persist:
            self.history.log_execution(
                result, project_id=tc.project_id, batch_id=batch_id,
                run_mode=run_mode, executed_by=self.executed_by,
            )
        return result

    def run_test_cases(self, test_cases: list, params: dict | None = None,
                       batch_id: str = DEFAULT_BATCH_ID, run_id: str = "",
                       run_mode: str = "ADHOC", persist: bool = True,
                       on_progress=None) -> list[ExecutionResult]:
        """
        Execute several saved test cases under one RUN_ID.

        One test blowing up never stops the rest — it is logged as ERROR and
        the run continues, which is the behaviour a nightly suite needs.
        """
        run_id = run_id or new_run_id()
        results = []
        for i, tc in enumerate(test_cases, start=1):
            result = self.run_test_case(tc, params=params, batch_id=batch_id,
                                        run_id=run_id, run_mode=run_mode, persist=persist)
            results.append(result)
            if on_progress:
                on_progress(i, len(test_cases), result)
        return results

    def run_folder(self, folder_id: str, params: dict | None = None,
                   batch_id: str = DEFAULT_BATCH_ID, run_mode: str = "FOLDER",
                   on_progress=None) -> list[ExecutionResult]:
        rows = self.repo.list_test_cases(folder_id=folder_id)
        cases = self.repo.get_test_cases([r["TEST_CASE_ID"] for r in rows])
        return self.run_test_cases(cases, params=params, batch_id=batch_id,
                                   run_mode=run_mode, on_progress=on_progress)

    def preview(self, sql: str, connection: str | None = None, params: dict | None = None,
                limit: int = 50):
        """Run a query without logging — powers the 'Test run' button in the editor."""
        res = self.connector(connection).query(sql, params or {})
        return res.columns, res.rows[:limit], res.row_count

    # ------------------------------------------------------------- internals
    def resolve_params(self, tc: TestCase, overrides: dict | None, batch_id: str) -> dict:
        """
        Build the parameter dict for a run.

        Precedence: caller overrides > the test case's saved defaults >
        built-ins. Every :placeholder the SQL uses must end up with a value,
        or the connector raises a clear error naming what's missing.
        """
        now = now_ist()
        merged: dict = {"batch_id": batch_id, "run_date": now.date(), "run_ts": now}
        merged.update(tc.param_defaults or {})
        merged.update({k: v for k, v in (overrides or {}).items() if v is not None})
        return merged

    def _dispatch(self, tc: TestCase, params: dict, result: ExecutionResult) -> None:
        handler = {
            "SOURCE_TARGET_COMPARE": self._run_compare,
            "ROW_COUNT_MATCH": self._run_row_count_match,
            "SQL_ROWCOUNT": self._run_rowcount_rule,
            "STATUS_COLUMN": self._run_status_column,
            "INFORMATIONAL": self._run_informational,
        }.get(tc.test_type)
        if handler is None:
            raise ValueError(f"Unknown test type '{tc.test_type}' on {tc.test_case_id}")
        handler(tc, params, result)

    def _run_compare(self, tc: TestCase, params: dict, result: ExecutionResult) -> None:
        src = self.connector(tc.source_connection).query(tc.source_sql, params)
        tgt = self.connector(tc.target_connection).query(tc.target_sql, params)
        report = compare_result_sets(
            src.columns, src.rows, tgt.columns, tgt.rows,
            key_columns=tc.key_column_list,
            compare_columns=tc.compare_column_list or None,
            tolerance=tc.tolerance or 0.0,
            max_failures=MAX_FAILURES,
        )
        result.source_row_count = report.source_row_count
        result.target_row_count = report.target_row_count
        result.matched_record_count = report.matched_record_count
        result.failed_record_count = report.failed_record_count
        result.failures = report.failures
        result.status = report.status
        result.columns = ["BUSINESS_KEY", "COLUMN_NAME", "EXPECTED_VALUE", "ACTUAL_VALUE",
                          "FAILURE_TYPE", "FAILURE_REASON"]
        result.rows = [(f.business_key, f.column_name, f.expected_value, f.actual_value,
                        f.failure_type, f.failure_reason) for f in report.failures]

    def _run_row_count_match(self, tc: TestCase, params: dict, result: ExecutionResult) -> None:
        src = self.connector(tc.source_connection).query(tc.source_sql, params)
        tgt = self.connector(tc.target_connection).query(tc.target_sql, params)
        src_n = _scalar_count(src)
        tgt_n = _scalar_count(tgt)
        report = compare_row_counts(src_n, tgt_n, tc.tolerance or 0.0)
        result.source_row_count = src_n
        result.target_row_count = tgt_n
        result.matched_record_count = report.matched_record_count
        result.failed_record_count = report.failed_record_count
        result.failures = report.failures
        result.status = report.status

    def _run_rowcount_rule(self, tc: TestCase, params: dict, result: ExecutionResult) -> None:
        res = self.connector(tc.source_connection).query(tc.validation_sql, params)
        result.source_row_count = res.row_count
        result.failed_record_count = res.row_count
        result.columns, result.rows = res.columns, res.rows[:MAX_FAILURES]
        result.status = "PASS" if res.row_count == 0 else "FAIL"
        result.failures = _rows_to_failures(res.columns, res.rows[:MAX_FAILURES], tc)

    def _run_status_column(self, tc: TestCase, params: dict, result: ExecutionResult) -> None:
        res = self.connector(tc.source_connection).query(tc.validation_sql, params)
        result.source_row_count = res.row_count
        result.columns, result.rows = res.columns, res.rows[:MAX_FAILURES]
        upper = [c.upper() for c in res.columns]
        status_idx = next((upper.index(a) for a in STATUS_ALIASES if a in upper), None)
        if status_idx is None:
            raise ValueError(
                "A STATUS_COLUMN test must return a column named STATUS "
                f"(got: {', '.join(res.columns) or 'no columns'})."
            )
        bad = [r for r in res.rows if str(r[status_idx]).strip().upper() not in ("PASS", "OK", "TRUE", "1")]
        result.failed_record_count = len(bad)
        result.matched_record_count = res.row_count - len(bad)
        result.status = "PASS" if not bad else "FAIL"
        result.failures = _rows_to_failures(res.columns, bad[:MAX_FAILURES], tc)

    def _run_informational(self, tc: TestCase, params: dict, result: ExecutionResult) -> None:
        res = self.connector(tc.source_connection).query(tc.validation_sql, params)
        result.source_row_count = res.row_count
        result.matched_record_count = res.row_count
        result.columns, result.rows = res.columns, res.rows[:MAX_FAILURES]
        result.status = "PASS"


def _scalar_count(res) -> int:
    """A count query may return COUNT(*) as one cell, or N rows to be counted."""
    if res.row_count == 1 and len(res.columns) == 1:
        try:
            return int(res.rows[0][0])
        except (TypeError, ValueError):
            pass
    return res.row_count


def _rows_to_failures(columns: list[str], rows: list[tuple], tc: TestCase) -> list[FailureRecord]:
    """
    Turn a rule query's result rows into mismatch detail.

    If the query names its columns using the recognised aliases
    (BUSINESS_KEY / COLUMN_NAME / EXPECTED_VALUE / ACTUAL_VALUE /
    FAILURE_REASON) those are used directly. Otherwise the first column
    becomes the business key and the whole row is rendered into the reason,
    so no failure is ever logged blank.
    """
    if not columns:
        return []
    upper = [c.upper() for c in columns]

    def idx(aliases):
        return next((upper.index(a) for a in aliases if a in upper), None)

    key_i, col_i = idx(KEY_ALIASES), idx(COLUMN_ALIASES)
    exp_i, act_i, reason_i = idx(EXPECTED_ALIASES), idx(ACTUAL_ALIASES), idx(REASON_ALIASES)
    if key_i is None:
        key_i = 0  # by convention the first selected column is the business key

    out = []
    for row in rows:
        detail = ", ".join(f"{c}={_render(v)}" for c, v in zip(columns, row))
        out.append(FailureRecord(
            business_key=_render(row[key_i]),
            column_name=(_render(row[col_i]) if col_i is not None
                         else (columns[1] if len(columns) > 1 else columns[0])),
            expected_value=_render(row[exp_i]) if exp_i is not None else "",
            actual_value=_render(row[act_i]) if act_i is not None else "",
            failure_type="RULE_VIOLATION",
            failure_reason=(_render(row[reason_i]) if reason_i is not None
                            else f"{tc.test_name}: {detail}")[:1000],
        ))
    return out


def _render(v) -> str:
    return "<NULL>" if v is None else str(v)
