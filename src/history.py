"""

Execution history store.

 

Writes and reads DQ_EXECUTION_LOG, DQ_FAILURE_LOG and DQ_REGRESSION_RUN —

the audit trail the brief requires, and the data behind the Execution

History screen (date-range filter, selection, rerun) and the dashboard.

"""

from __future__ import annotations

 

import json

import uuid

from dataclasses import dataclass, field

from datetime import date, datetime, time

 

from src.config import DEFAULT_USER, now_ist

 

def _now() -> datetime:

    return now_ist()

 

def _uid() -> str:

    return uuid.uuid4().hex

 

def new_run_id() -> str:

    return f"RUN_{now_ist().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

 

def day_start(d) -> datetime:

    """Coerce a date/datetime/'YYYY-MM-DD' to 00:00:00 of that day."""

    if isinstance(d, datetime):

        return d

    if isinstance(d, date):

        return datetime.combine(d, time.min)

    return datetime.combine(date.fromisoformat(str(d)[:10]), time.min)

 

def day_end(d) -> datetime:

    """Coerce to 23:59:59.999999 so a same-day start/end filter is inclusive."""

    if isinstance(d, datetime) and (d.hour or d.minute or d.second):

        return d

    base = d.date() if isinstance(d, datetime) else (

        d if isinstance(d, date) else date.fromisoformat(str(d)[:10]))

    return datetime.combine(base, time.max)

 

@dataclass

class FailureRecord:

    business_key: str = ""

    column_name: str = ""

    expected_value: str = ""

    actual_value: str = ""

    failure_type: str = "RULE_VIOLATION"

    failure_reason: str = ""

 

@dataclass

class ExecutionResult:

    """Everything one test-case execution produced."""

    execution_id: str = ""

    run_id: str = ""

    test_case_id: str = ""

    test_name: str = ""

    folder_path: str = ""

    status: str = "PASS"                     # PASS | FAIL | ERROR

    source_row_count: int = 0

    target_row_count: int = 0

    matched_record_count: int = 0

    failed_record_count: int = 0

    duration_ms: int = 0

    error_message: str = ""

    failures: list[FailureRecord] = field(default_factory=list)

    columns: list[str] = field(default_factory=list)

    rows: list[tuple] = field(default_factory=list)

    params: dict = field(default_factory=dict)

    test_version_no: int = 1

 

    @property

    def passed(self) -> bool:

        return self.status == "PASS"

 

class HistoryStore:

    def __init__(self, connector):

        self.db = connector

 

    # ------------------------------------------------------------ write path

    def log_execution(self, result: ExecutionResult, project_id: str = "", batch_id: str = "",

                      run_mode: str = "ADHOC", executed_by: str = "") -> str:

        result.execution_id = result.execution_id or _uid()

        self.db.execute(

            """INSERT INTO DQ_EXECUTION_LOG (

                   EXECUTION_ID, RUN_ID, TEST_CASE_ID, TEST_NAME, PROJECT_ID, FOLDER_PATH,

                   TEST_VERSION_NO, BATCH_ID, RUN_TS, STATUS, SOURCE_ROW_COUNT, TARGET_ROW_COUNT,

                   MATCHED_RECORD_COUNT, FAILED_RECORD_COUNT, DURATION_MS, RUN_MODE,

                   PARAMS_JSON, ERROR_MESSAGE, EXECUTED_BY)

               VALUES (:eid, :run_id, :tcid, :tname, :pid, :fpath, :vno, :bid, :ts, :status,

                       :src, :tgt, :matched, :failed, :dur, :mode, :params, :err, :by)""",

            {"eid": result.execution_id, "run_id": result.run_id, "tcid": result.test_case_id,

             "tname": result.test_name[:200], "pid": project_id, "fpath": result.folder_path,

             "vno": result.test_version_no, "bid": batch_id, "ts": _now(), "status": result.status,

             "src": result.source_row_count, "tgt": result.target_row_count,

             "matched": result.matched_record_count, "failed": result.failed_record_count,

             "dur": result.duration_ms, "mode": run_mode,

             "params": json.dumps(result.params, default=str)[:8000],

             "err": (result.error_message or "")[:4000], "by": executed_by or DEFAULT_USER},

        )

        if result.failures:

            self.log_failures(result, batch_id)

        self.db.commit()

        return result.execution_id

 

    def log_failures(self, result: ExecutionResult, batch_id: str = "", cap: int = 1000) -> int:

        """Persist mismatch detail. Capped so one broken join can't write a million rows."""

        rows = result.failures[:cap]

        for f in rows:

            self.db.execute(

                """INSERT INTO DQ_FAILURE_LOG (

                       FAILURE_ID, EXECUTION_ID, TEST_CASE_ID, BATCH_ID, BUSINESS_KEY, COLUMN_NAME,

                       EXPECTED_VALUE, ACTUAL_VALUE, FAILURE_TYPE, FAILURE_REASON, FAILED_TS)

                   VALUES (:fid, :eid, :tcid, :bid, :bk, :col, :exp, :act, :ftype, :reason, :ts)""",

                {"fid": _uid(), "eid": result.execution_id, "tcid": result.test_case_id,

                 "bid": batch_id, "bk": str(f.business_key)[:500], "col": str(f.column_name)[:200],

                 "exp": _clip(f.expected_value), "act": _clip(f.actual_value),

                 "ftype": f.failure_type, "reason": str(f.failure_reason)[:1000], "ts": _now()},

            )

        return len(rows)

 

    def start_regression_run(self, run_id: str, project_id: str = "", folder_path: str = "",

                             triggered_from: str = "CLI", selected_ids: list[str] | None = None,

                             history_start=None, history_end=None, executed_by: str = "") -> str:

        rid = _uid()

        self.db.execute(

            """INSERT INTO DQ_REGRESSION_RUN (

                   REGRESSION_ID, RUN_ID, PROJECT_ID, FOLDER_PATH, TRIGGERED_FROM,

                   SELECTED_TEST_IDS, HISTORY_START, HISTORY_END, TOTAL_TESTS,

                   PASSED_TESTS, FAILED_TESTS, ERROR_TESTS, STARTED_TS, EXECUTED_BY)

               VALUES (:rid, :run_id, :pid, :fpath, :from, :ids, :hstart, :hend, :total,

                       0, 0, 0, :ts, :by)""",

            {"rid": rid, "run_id": run_id, "pid": project_id, "fpath": folder_path,

             "from": triggered_from, "ids": ",".join(selected_ids or [])[:4000],

             "hstart": day_start(history_start) if history_start else None,

             "hend": day_end(history_end) if history_end else None,

             "total": len(selected_ids or []), "ts": _now(),

             "by": executed_by or DEFAULT_USER},

        )

        self.db.commit()

        return rid

 

    def complete_regression_run(self, regression_id: str, results: list[ExecutionResult]) -> None:

        self.db.execute(

            """UPDATE DQ_REGRESSION_RUN SET

                   TOTAL_TESTS = :total, PASSED_TESTS = :passed, FAILED_TESTS = :failed,

                   ERROR_TESTS = :errored, COMPLETED_TS = :ts

               WHERE REGRESSION_ID = :rid""",

            {"total": len(results),

             "passed": sum(1 for r in results if r.status == "PASS"),

             "failed": sum(1 for r in results if r.status == "FAIL"),

             "errored": sum(1 for r in results if r.status == "ERROR"),

             "ts": _now(), "rid": regression_id},

        )

        self.db.commit()

 

    # ------------------------------------------------------------- read path

    def get_execution_history(self, start, end, project_id: str = "", folder_path: str = "",

                              status: str = "", test_case_id: str = "",

                              latest_per_test: bool = False, limit: int = 5000) -> list[dict]:

        """

        The Execution History screen: every run between two dates, newest first.

 

        `start` and `end` accept a date, datetime or 'YYYY-MM-DD'. A bare end

        date is widened to end-of-day so a single-day filter returns that day.

        """

        sql = """SELECT E.EXECUTION_ID, E.RUN_ID, E.TEST_CASE_ID, E.TEST_NAME, E.FOLDER_PATH,

                        E.TEST_VERSION_NO, E.BATCH_ID, E.RUN_TS, E.STATUS,

                        E.SOURCE_ROW_COUNT, E.TARGET_ROW_COUNT, E.MATCHED_RECORD_COUNT,

                        E.FAILED_RECORD_COUNT, E.DURATION_MS, E.RUN_MODE, E.EXECUTED_BY,

                        E.ERROR_MESSAGE

                 FROM DQ_EXECUTION_LOG E

                 WHERE E.RUN_TS >= :start AND E.RUN_TS <= :end"""

        params = {"start": day_start(start), "end": day_end(end)}

        if project_id:

            sql += " AND E.PROJECT_ID = :pid"

            params["pid"] = project_id

        if folder_path:

            sql += " AND E.FOLDER_PATH LIKE :fpath"

            params["fpath"] = folder_path.rstrip("/") + "%"

        if status:

            sql += " AND E.STATUS = :status"

            params["status"] = status

        if test_case_id:

            sql += " AND E.TEST_CASE_ID = :tcid"

            params["tcid"] = test_case_id

        if latest_per_test:

            # Subquery form rather than QUALIFY: QUALIFY is Snowflake-only,

            # and this app is meant to also run against MSSQL.

            sql = f"""SELECT * FROM (

                          SELECT H.*, ROW_NUMBER() OVER (

                              PARTITION BY H.TEST_CASE_ID

                              ORDER BY H.RUN_TS DESC, H.EXECUTION_ID) AS RN

                          FROM ({sql}) H

                      ) R WHERE R.RN = 1"""

            sql += f" ORDER BY RUN_TS DESC LIMIT {int(limit)}"

        else:

            sql += f" ORDER BY E.RUN_TS DESC LIMIT {int(limit)}"

        return self.db.query(sql, params).to_dicts()

 

    def get_failures(self, execution_id: str = "", test_case_id: str = "",

                     run_id: str = "", limit: int = 2000) -> list[dict]:

        """Mismatch detail. Scoped to ONE execution by default, so corrected

        records stop appearing the moment a test is rerun."""

        sql = """SELECT F.TEST_CASE_ID, F.BUSINESS_KEY, F.COLUMN_NAME, F.EXPECTED_VALUE,

                        F.ACTUAL_VALUE, F.FAILURE_TYPE, F.FAILURE_REASON, F.FAILED_TS, F.EXECUTION_ID

                 FROM DQ_FAILURE_LOG F WHERE 1 = 1"""

        params: dict = {}

        if execution_id:

            sql += " AND F.EXECUTION_ID = :eid"

            params["eid"] = execution_id

        if test_case_id:

            sql += " AND F.TEST_CASE_ID = :tcid"

            params["tcid"] = test_case_id

        if run_id:

            sql += """ AND F.EXECUTION_ID IN (

                           SELECT EXECUTION_ID FROM DQ_EXECUTION_LOG WHERE RUN_ID = :rid)"""

            params["rid"] = run_id

        sql += f" ORDER BY F.FAILED_TS DESC, F.BUSINESS_KEY LIMIT {int(limit)}"

        return self.db.query(sql, params).to_dicts()

 

    def latest_failures_for_project(self, project_id: str, limit: int = 2000) -> list[dict]:

        """Failure detail from each test's most recent run only."""

        return self.db.query(

            f"""WITH RANKED AS (

                    SELECT EXECUTION_ID, ROW_NUMBER() OVER (

                               PARTITION BY TEST_CASE_ID

                               ORDER BY RUN_TS DESC, EXECUTION_ID) AS RN

                    FROM DQ_EXECUTION_LOG

                    WHERE PROJECT_ID = :pid),

                LATEST AS (SELECT EXECUTION_ID FROM RANKED WHERE RN = 1)

                SELECT F.TEST_CASE_ID, F.BUSINESS_KEY, F.COLUMN_NAME, F.EXPECTED_VALUE,

                       F.ACTUAL_VALUE, F.FAILURE_TYPE, F.FAILURE_REASON, F.FAILED_TS

                FROM DQ_FAILURE_LOG F JOIN LATEST L ON L.EXECUTION_ID = F.EXECUTION_ID

                ORDER BY F.FAILED_TS DESC LIMIT {int(limit)}""",

            {"pid": project_id},

        ).to_dicts()

 

    def dashboard_summary(self, project_id: str = "") -> dict:

        """Pass/fail counts from the LATEST run of each test case."""

        sql = """SELECT STATUS, COUNT(*) AS N, SUM(FAILED_RECORD_COUNT) AS FAILED_RECORDS

                 FROM (

                     SELECT TEST_CASE_ID, STATUS, FAILED_RECORD_COUNT,

                            ROW_NUMBER() OVER (PARTITION BY TEST_CASE_ID

                                               ORDER BY RUN_TS DESC, EXECUTION_ID) AS RN

                     FROM DQ_EXECUTION_LOG

                     WHERE 1 = 1 {proj}

                 ) R WHERE R.RN = 1

                 GROUP BY STATUS"""

        params: dict = {}

        if project_id:

            params["pid"] = project_id

        sql = sql.format(proj=" AND PROJECT_ID = :pid" if project_id else "")

        rows = self.db.query(sql, params).to_dicts()

        counts = {r["STATUS"]: int(r["N"] or 0) for r in rows}

        failed_records = sum(int(r["FAILED_RECORDS"] or 0) for r in rows)

        passed, failed, errored = counts.get("PASS", 0), counts.get("FAIL", 0), counts.get("ERROR", 0)

        total = passed + failed + errored

        return {

            "total": total, "passed": passed, "failed": failed, "errored": errored,

            "failed_records": failed_records,

            "pass_pct": round(passed / total * 100, 1) if total else None,

        }

 

    def regression_runs(self, project_id: str = "", limit: int = 50) -> list[dict]:

        sql = """SELECT REGRESSION_ID, RUN_ID, FOLDER_PATH, TRIGGERED_FROM, TOTAL_TESTS,

                        PASSED_TESTS, FAILED_TESTS, ERROR_TESTS, STARTED_TS, COMPLETED_TS, EXECUTED_BY

                 FROM DQ_REGRESSION_RUN WHERE 1 = 1"""

        params: dict = {}

        if project_id:

            sql += " AND PROJECT_ID = :pid"

            params["pid"] = project_id

        sql += f" ORDER BY STARTED_TS DESC LIMIT {int(limit)}"

        return self.db.query(sql, params).to_dicts()

 

def _clip(v, n: int = 4000) -> str:

    return "" if v is None else str(v)[:n]