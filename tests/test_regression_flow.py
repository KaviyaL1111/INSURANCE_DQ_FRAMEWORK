"""
The workflow the brief specifies, end to end.

Execution history -> date filter -> select -> rerun at the latest saved
configuration -> mismatch detail -> correct -> rerun -> all pass.
"""
from datetime import date, timedelta

import pytest

from src.regression_engine import RegressionEngine
from src.repository import TestCase


@pytest.fixture
def reg(repo):
    eng = RegressionEngine(repository=repo, executed_by="pytest")
    eng.engine._connectors["snowflake"] = repo.db
    eng.engine._connectors["sqlite"] = repo.db
    yield eng
    eng.close()


@pytest.fixture
def suite(repo, project, demo_tables):
    """A project with a /Source-to-Target folder and a saved /Regression copy."""
    s2t = repo.create_folder(project["PROJECT_ID"], "Source-to-Target")
    regression = repo.create_folder(project["PROJECT_ID"], "Regression",
                                    folder_type="REGRESSION")
    compare = repo.create_test_case(TestCase(
        folder_id=s2t["FOLDER_ID"], test_name="Policy source vs target",
        test_type="SOURCE_TARGET_COMPARE", key_columns="POLICY_ID",
        compare_columns="POLICY_TYPE, PREMIUM_AMOUNT, STATUS",
        source_connection="sqlite", target_connection="sqlite",
        source_sql="SELECT POLICY_ID, POLICY_TYPE, PREMIUM_AMOUNT, STATUS FROM SRC_POLICY",
        target_sql="SELECT POLICY_ID, POLICY_TYPE, PREMIUM_AMOUNT, STATUS FROM TGT_POLICY"))
    orphan = repo.create_test_case(TestCase(
        folder_id=s2t["FOLDER_ID"], test_name="No unexpected target rows",
        test_type="SQL_ROWCOUNT", source_connection="sqlite",
        validation_sql="""SELECT T.POLICY_ID AS BUSINESS_KEY, 'POLICY_ID' AS COLUMN_NAME,
                                 'present in source' AS EXPECTED_VALUE,
                                 T.POLICY_ID AS ACTUAL_VALUE,
                                 'Target row has no source' AS FAILURE_REASON
                          FROM TGT_POLICY T
                          LEFT JOIN SRC_POLICY S ON S.POLICY_ID = T.POLICY_ID
                          WHERE S.POLICY_ID IS NULL"""))
    copy = repo.copy_test_case(compare.test_case_id, regression["FOLDER_ID"],
                               new_name="[Regression] Policy source vs target")
    return {"project": project, "s2t": s2t, "regression": regression,
            "compare": compare, "orphan": orphan, "copy": copy}


def corrupt(db):
    """The brief's Notes: introduce a few intentional mismatches."""
    db.execute("UPDATE TGT_POLICY SET PREMIUM_AMOUNT = 10000.00 WHERE POLICY_ID = 'P1005'")
    db.execute("UPDATE TGT_POLICY SET STATUS = 'Pending' WHERE POLICY_ID = 'P1002'")
    db.execute("INSERT INTO TGT_POLICY VALUES ('P9999', 'CX', 'Auto', 1.0, 'Active')")
    db.commit()


def repair(db):
    db.execute("UPDATE TGT_POLICY SET PREMIUM_AMOUNT = 12000.00 WHERE POLICY_ID = 'P1005'")
    db.execute("UPDATE TGT_POLICY SET STATUS = 'Inactive' WHERE POLICY_ID = 'P1002'")
    db.execute("DELETE FROM TGT_POLICY WHERE POLICY_ID = 'P9999'")
    db.commit()


class TestHistory:
    def test_date_range_filter(self, reg, suite, db):
        reg.engine.run_test_case(suite["compare"])
        today = date.today()
        assert len(reg.get_execution_history(today, today)) == 1
        past = today - timedelta(days=10)
        assert reg.get_execution_history(past, past - timedelta(days=1)) == []

    def test_same_day_start_and_end_finds_todays_runs(self, reg, suite):
        """A bare end date must cover the whole day, not truncate to midnight."""
        reg.engine.run_test_case(suite["compare"])
        today = date.today().isoformat()
        assert len(reg.get_execution_history(today, today)) == 1

    def test_status_filter(self, reg, suite, db):
        corrupt(db)
        reg.engine.run_test_cases([suite["compare"], suite["orphan"]])
        today = date.today()
        assert len(reg.get_execution_history(today, today, status="FAIL")) == 2
        assert reg.get_execution_history(today, today, status="PASS") == []

    def test_latest_per_test_collapses_repeats(self, reg, suite):
        for _ in range(3):
            reg.engine.run_test_case(suite["compare"])
        today = date.today()
        assert len(reg.get_execution_history(today, today)) == 3
        assert len(reg.get_execution_history(today, today, latest_per_test=True)) == 1


class TestSelectiveRerun:
    def test_rerun_uses_the_latest_saved_configuration(self, reg, repo, suite, db):
        """
        The point of the whole feature: edit a test case after it failed, then
        rerun it from history — the corrected definition must be what runs.
        """
        tc = suite["orphan"]
        db.execute("INSERT INTO TGT_POLICY VALUES ('P8888', 'CY', 'Auto', 1.0, 'Active')")
        db.commit()
        assert reg.engine.run_test_case(tc).status == "FAIL"

        # An analyst decides P8888 is legitimately expected and edits the test.
        tc.validation_sql += " AND T.POLICY_ID <> 'P8888'"
        repo.update_test_case(tc, change_note="allow the known exception")

        outcome = reg.rerun_selected([tc.test_case_id])
        assert outcome.all_passed
        assert outcome.results[0].test_version_no == 2

    def test_duplicate_selection_runs_each_test_once(self, reg, suite):
        """History lists one row per execution; picking three runs of one test
        should still execute it once."""
        for _ in range(3):
            reg.engine.run_test_case(suite["compare"])
        tcid = suite["compare"].test_case_id
        outcome = reg.rerun_selected([tcid, tcid, tcid])
        assert outcome.total == 1

    def test_deleted_test_case_is_skipped_not_fatal(self, reg, repo, suite):
        reg.engine.run_test_case(suite["compare"])
        repo.delete_test_case(suite["compare"].test_case_id)
        outcome = reg.rerun_selected([suite["compare"].test_case_id, suite["orphan"].test_case_id])
        assert outcome.total == 1
        assert outcome.skipped[0][0] == suite["compare"].test_case_id

    def test_rerun_only_what_failed_in_a_window(self, reg, suite, db):
        corrupt(db)
        reg.engine.run_test_cases([suite["compare"], suite["orphan"]])
        repair(db)
        today = date.today()
        outcome = reg.rerun_failed_in_window(today, today, project_id=suite["project"]["PROJECT_ID"])
        assert outcome.total == 2 and outcome.all_passed

    def test_rerun_is_recorded_for_audit(self, reg, suite, db):
        reg.engine.run_test_case(suite["compare"])
        outcome = reg.rerun_selected([suite["compare"].test_case_id])
        runs = reg.history.regression_runs()
        assert len(runs) == 1
        assert runs[0]["TOTAL_TESTS"] == 1
        assert runs[0]["PASSED_TESTS"] == 1
        assert runs[0]["COMPLETED_TS"] is not None
        assert runs[0]["TRIGGERED_FROM"] == "EXECUTION_HISTORY"


class TestTheBriefsWalkthrough:
    def test_corrupt_detect_fix_rerun_all_pass(self, reg, suite, db):
        """
        The exact sequence from the brief's Notes section:
        introduce mismatches -> validate -> see failed records with detail ->
        correct them -> rerun the saved script from /Regression -> all pass.
        """
        pid = suite["project"]["PROJECT_ID"]

        # 1. introduce a few intentional mismatches, then validate
        corrupt(db)
        first = reg.run_full_suite(pid)
        assert first.failed == 3, [r.status for r in first.results]

        # 2. the results show the failed records WITH mismatch detail
        compare_run = next(r for r in first.results
                           if r.test_case_id == suite["compare"].test_case_id)
        detail = {(f.business_key, f.column_name): (f.expected_value, f.actual_value)
                  for f in compare_run.failures}
        assert detail[("P1005", "PREMIUM_AMOUNT")] == ("12000", "10000")
        assert detail[("P1002", "STATUS")] == ("Inactive", "Pending")
        assert ("P9999", "POLICY_ID") in detail

        # 3. correct the mismatches
        repair(db)

        # 4. rerun the saved script from the Regression folder
        rerun = reg.run_regression_folder(pid, "/Regression")
        assert rerun.total == 1 and rerun.all_passed

        # 5. and the whole suite is green
        final = reg.run_full_suite(pid)
        assert final.all_passed, [(r.test_name, r.status) for r in final.results]

        # 6. the dashboard reflects the CORRECTED state, not the historical failures
        summary = reg.history.dashboard_summary(pid)
        assert summary["failed"] == 0
        assert summary["pass_pct"] == 100.0

        # 7. but the audit trail still holds every run
        today = date.today()
        assert len(reg.get_execution_history(today, today)) > final.total

    def test_regression_folder_missing_gives_a_helpful_error(self, reg, suite):
        with pytest.raises(LookupError, match="no folder"):
            reg.run_regression_folder(suite["project"]["PROJECT_ID"], "/Nope")


class TestFullSuiteScope:
    def test_full_suite_runs_every_active_test_case(self, reg, repo, project):
        """The suite count must match the project's test-case count (was 20 of 21)."""
        folder = repo.create_folder(project["PROJECT_ID"], "Mixed")
        for name, is_regression in (("rule", True), ("report", False)):
            repo.create_test_case(TestCase(
                project_id=project["PROJECT_ID"], folder_id=folder["FOLDER_ID"],
                test_name=name, test_type="INFORMATIONAL", source_connection="sqlite",
                validation_sql="SELECT 1 AS X", is_regression=is_regression))
        total = len(repo.list_test_cases(project_id=project["PROJECT_ID"]))

        assert reg.run_full_suite(project["PROJECT_ID"]).total == total == 2
        only = reg.run_full_suite(project["PROJECT_ID"], regression_only=True)
        assert [r.test_name for r in only.results] == ["rule"]
