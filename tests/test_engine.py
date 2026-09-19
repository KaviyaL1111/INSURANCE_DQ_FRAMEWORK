"""Execution engine: the five test types, error handling, and logging."""
import pytest

from src.repository import TestCase


@pytest.fixture
def folder(repo, project):
    return repo.create_folder(project["PROJECT_ID"], "Checks")


def save(repo, folder, **kw):
    tc = TestCase(folder_id=folder["FOLDER_ID"],
                  test_name=kw.pop("name", "a test"),
                  source_connection="sqlite", target_connection="sqlite", **kw)
    return repo.create_test_case(tc)


class TestRuleQueries:
    def test_zero_rows_passes(self, repo, engine, folder, demo_tables):
        tc = save(repo, folder, test_type="SQL_ROWCOUNT",
                  validation_sql="SELECT POLICY_ID FROM SRC_POLICY WHERE PREMIUM_AMOUNT < 0")
        assert engine.run_test_case(tc).status == "PASS"

    def test_rows_returned_fails_with_detail(self, repo, engine, folder, demo_tables):
        tc = save(repo, folder, test_type="SQL_ROWCOUNT",
                  validation_sql="""SELECT POLICY_ID AS BUSINESS_KEY,
                                           'PREMIUM_AMOUNT' AS COLUMN_NAME,
                                           '< 20000' AS EXPECTED_VALUE,
                                           PREMIUM_AMOUNT AS ACTUAL_VALUE,
                                           'Premium is too high' AS FAILURE_REASON
                                    FROM SRC_POLICY WHERE PREMIUM_AMOUNT > 15000""")
        result = engine.run_test_case(tc)
        assert result.status == "FAIL"
        assert result.failed_record_count == 2
        f = result.failures[0]
        assert f.business_key in ("P1001", "P1004")
        assert f.expected_value == "< 20000"
        assert f.failure_reason == "Premium is too high"

    def test_unnamed_columns_still_produce_readable_detail(self, repo, engine, folder, demo_tables):
        """A rule query with no recognised aliases must not log blank failures."""
        tc = save(repo, folder, test_type="SQL_ROWCOUNT", name="plain",
                  validation_sql="SELECT POLICY_ID, PREMIUM_AMOUNT FROM SRC_POLICY WHERE POLICY_ID = 'P1005'")
        f = engine.run_test_case(tc).failures[0]
        assert f.business_key == "P1005"
        assert "PREMIUM_AMOUNT=12000" in f.failure_reason


class TestSourceTargetCompare:
    def _tc(self, repo, folder, **kw):
        return save(repo, folder, test_type="SOURCE_TARGET_COMPARE", key_columns="POLICY_ID",
                    source_sql="SELECT POLICY_ID, POLICY_TYPE, PREMIUM_AMOUNT FROM SRC_POLICY",
                    target_sql="SELECT POLICY_ID, POLICY_TYPE, PREMIUM_AMOUNT FROM TGT_POLICY",
                    **kw)

    def test_identical_tables_pass(self, repo, engine, folder, demo_tables):
        r = engine.run_test_case(self._tc(repo, folder))
        assert r.status == "PASS"
        assert r.matched_record_count == 5 and r.failed_record_count == 0

    def test_value_mismatch_is_reported_with_expected_and_actual(self, repo, engine, folder,
                                                                 demo_tables, db):
        db.execute("UPDATE TGT_POLICY SET PREMIUM_AMOUNT = 10000.00 WHERE POLICY_ID = 'P1005'")
        db.commit()
        r = engine.run_test_case(self._tc(repo, folder))
        assert r.status == "FAIL"
        f = next(f for f in r.failures if f.business_key == "P1005")
        assert (f.column_name, f.expected_value, f.actual_value) == \
               ("PREMIUM_AMOUNT", "12000", "10000")
        assert f.failure_type == "VALUE_MISMATCH"

    def test_record_missing_from_target(self, repo, engine, folder, demo_tables, db):
        db.execute("DELETE FROM TGT_POLICY WHERE POLICY_ID = 'P1003'")
        db.commit()
        r = engine.run_test_case(self._tc(repo, folder))
        f = next(f for f in r.failures if f.business_key == "P1003")
        assert f.failure_type == "MISSING_IN_TARGET"
        assert r.source_row_count == 5 and r.target_row_count == 4

    def test_unexpected_record_in_target(self, repo, engine, folder, demo_tables, db):
        db.execute("INSERT INTO TGT_POLICY VALUES ('P9999', 'C9', 'Auto', 1.0, 'Active')")
        db.commit()
        r = engine.run_test_case(self._tc(repo, folder))
        assert next(f for f in r.failures if f.business_key == "P9999").failure_type == \
            "EXTRA_IN_TARGET"

    def test_tolerance_absorbs_rounding(self, repo, engine, folder, demo_tables, db):
        db.execute("UPDATE TGT_POLICY SET PREMIUM_AMOUNT = 12000.004 WHERE POLICY_ID = 'P1005'")
        db.commit()
        assert engine.run_test_case(self._tc(repo, folder, name="tol", tolerance=0.01)).status == "PASS"


class TestOtherTypes:
    def test_row_count_match(self, repo, engine, folder, demo_tables, db):
        tc = save(repo, folder, test_type="ROW_COUNT_MATCH",
                  source_sql="SELECT COUNT(*) FROM SRC_POLICY",
                  target_sql="SELECT COUNT(*) FROM TGT_POLICY")
        assert engine.run_test_case(tc).status == "PASS"
        db.execute("DELETE FROM TGT_POLICY WHERE POLICY_ID = 'P1001'")
        db.commit()
        r = engine.run_test_case(tc)
        assert r.status == "FAIL" and r.source_row_count == 5 and r.target_row_count == 4

    def test_status_column(self, repo, engine, folder, demo_tables):
        ok = save(repo, folder, test_type="STATUS_COLUMN", name="ok",
                  validation_sql="SELECT 'PASS' AS STATUS")
        bad = save(repo, folder, test_type="STATUS_COLUMN", name="bad",
                   validation_sql="SELECT 'FAIL' AS STATUS")
        assert engine.run_test_case(ok).status == "PASS"
        assert engine.run_test_case(bad).status == "FAIL"

    def test_status_column_without_a_status_column_errors_clearly(self, repo, engine, folder):
        tc = save(repo, folder, test_type="STATUS_COLUMN", name="nostatus",
                  validation_sql="SELECT 1 AS X")
        r = engine.run_test_case(tc)
        assert r.status == "ERROR" and "STATUS" in r.error_message

    def test_informational_never_fails(self, repo, engine, folder, demo_tables):
        """A reporting query returns rows by design — it must not be scored as a failure."""
        tc = save(repo, folder, test_type="INFORMATIONAL",
                  validation_sql="SELECT * FROM SRC_POLICY")
        r = engine.run_test_case(tc)
        assert r.status == "PASS" and r.source_row_count == 5


class TestParameters:
    def test_defaults_are_applied(self, repo, engine, folder, demo_tables):
        tc = save(repo, folder, test_type="SQL_ROWCOUNT",
                  validation_sql="SELECT POLICY_ID FROM SRC_POLICY WHERE POLICY_ID = :pid")
        tc.param_defaults = {"pid": "P1001"}
        tc = repo.update_test_case(tc)
        assert engine.run_test_case(tc).failed_record_count == 1

    def test_runtime_override_beats_the_default(self, repo, engine, folder, demo_tables):
        tc = save(repo, folder, test_type="SQL_ROWCOUNT",
                  validation_sql="SELECT POLICY_ID FROM SRC_POLICY WHERE POLICY_ID = :pid")
        tc.param_defaults = {"pid": "P1001"}
        tc = repo.update_test_case(tc)
        assert engine.run_test_case(tc, params={"pid": "NOPE"}).failed_record_count == 0

    def test_missing_parameter_gives_a_named_error(self, repo, engine, folder, demo_tables):
        tc = save(repo, folder, test_type="SQL_ROWCOUNT",
                  validation_sql="SELECT POLICY_ID FROM SRC_POLICY WHERE POLICY_ID = :who")
        r = engine.run_test_case(tc)
        assert r.status == "ERROR" and "who" in r.error_message


class TestErrorHandling:
    def test_broken_sql_is_logged_as_error_not_a_crash(self, repo, engine, folder):
        tc = save(repo, folder, test_type="SQL_ROWCOUNT",
                  validation_sql="SELECT * FROM TABLE_THAT_DOES_NOT_EXIST")
        r = engine.run_test_case(tc)
        assert r.status == "ERROR" and r.error_message

    def test_one_broken_test_does_not_stop_the_run(self, repo, engine, folder, demo_tables):
        broken = save(repo, folder, name="broken", test_type="SQL_ROWCOUNT",
                      validation_sql="SELECT * FROM NOPE")
        good = save(repo, folder, name="good", test_type="SQL_ROWCOUNT",
                    validation_sql="SELECT 1 WHERE 1 = 0")
        results = engine.run_test_cases([broken, good])
        assert [r.status for r in results] == ["ERROR", "PASS"]


class TestLogging:
    def test_execution_and_failures_are_recorded(self, repo, engine, folder, demo_tables, db):
        db.execute("UPDATE TGT_POLICY SET POLICY_TYPE = 'WRONG' WHERE POLICY_ID = 'P1001'")
        db.commit()
        tc = save(repo, folder, test_type="SOURCE_TARGET_COMPARE", key_columns="POLICY_ID",
                  source_sql="SELECT POLICY_ID, POLICY_TYPE FROM SRC_POLICY",
                  target_sql="SELECT POLICY_ID, POLICY_TYPE FROM TGT_POLICY")
        result = engine.run_test_case(tc)
        logged = db.query("SELECT * FROM DQ_EXECUTION_LOG").to_dicts()
        assert len(logged) == 1
        assert logged[0]["STATUS"] == "FAIL"
        assert logged[0]["TEST_VERSION_NO"] == 1
        failures = engine.history.get_failures(execution_id=result.execution_id)
        assert failures[0]["EXPECTED_VALUE"] == "Automobile"
        assert failures[0]["ACTUAL_VALUE"] == "WRONG"

    def test_runs_share_a_run_id(self, repo, engine, folder, demo_tables):
        a = save(repo, folder, name="a", test_type="SQL_ROWCOUNT", validation_sql="SELECT 1 WHERE 1=0")
        b = save(repo, folder, name="b", test_type="SQL_ROWCOUNT", validation_sql="SELECT 1 WHERE 1=0")
        results = engine.run_test_cases([a, b])
        assert results[0].run_id == results[1].run_id
