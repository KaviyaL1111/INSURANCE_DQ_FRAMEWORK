"""
Flat file -> staging -> transformation -> curated, then the curated
validations (source-to-target, historical, incremental) on that batch.

The pipeline scripts are Snowflake SQL, so these run them on DuckDB (see
duckdb_support.py) while the repository stays on SQLite. They skip when the
`duckdb` package isn't installed.
"""
import pytest

pytest.importorskip("duckdb")

from duckdb_support import DuckDBConnector                    # noqa: E402

from src import etl_loader                                     # noqa: E402
from src.curated import (check_for_promotion, promote_to_curated,  # noqa: E402
                         validate_curated, validation_window)
from src.dq_engine import DQEngine                             # noqa: E402
from src.seed import seed_catalog                              # noqa: E402
from src.staging import load_to_staging, read_file_as_text     # noqa: E402

B1, B2, B3 = "BATCH_20260901_001", "BATCH_20260915_002", "BATCH_20260920_003"
FILES = {"STG_CUSTOMER": "data/customers.csv", "STG_POLICY": "data/policies.csv",
         "STG_CLAIM": "data/claims.csv"}


@pytest.fixture
def duck():
    conn = DuckDBConnector()
    etl_loader.init_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def suite(repo, duck):
    """The seeded catalog, run against the DuckDB pipeline tables."""
    seed_catalog(repo=repo)
    eng = DQEngine(repository=repo, executed_by="pytest")
    eng._connectors["snowflake"] = duck
    pid = repo.find_project_by_name("Insurance Policy & Claim")["PROJECT_ID"]
    yield eng, pid
    eng.close()


def read(table):
    return read_file_as_text(FILES[table])


def stage(duck, table, batch, cols, rows):
    load_to_staging(duck, table, cols, rows, batch, source_file=f"{table}.csv")


def stage_demo_files(duck, batch=B1):
    for table in FILES:
        stage(duck, table, batch, *read(table))


def with_column(cols, rows, column, value, n=None):
    """Copy of `rows` (the first n) with `column` set to `value`."""
    i = [c.upper() for c in cols].index(column)
    return [tuple(value if j == i else v for j, v in enumerate(r)) for r in rows[:n]]


def outcome(results_by_folder):
    """{test name: (status, [(failure type, key, column)])} across the three folders."""
    out = {}
    for results in results_by_folder.values():
        for r in results:
            out[r.test_name] = (r.status, [(f.failure_type, f.business_key, f.column_name)
                                           for f in r.failures], r.error_message)
    return out


def failing(results_by_folder):
    return {name: v for name, v in outcome(results_by_folder).items() if v[0] != "PASS"}


def count(duck, sql, **params):
    return int(duck.query(sql, params).rows[0][0])


class TestPromotion:
    def test_uploaded_files_reach_curated_by_the_mapping_rules(self, duck):
        stage_demo_files(duck)
        out = promote_to_curated(duck, B1)
        assert out["transformed"] == {"TRN_CUSTOMER": 30, "TRN_POLICY": 40, "TRN_CLAIM": 50}
        assert out["curated"] == {"CUSTOMER_360": 30, "POLICY_MASTER": 40, "CLAIM_MASTER": 50}
        # POL-001 / POL-002 decodes, CUS-002 / CUS-003 standardisation
        assert duck.query("SELECT POLICY_TYPE, POLICY_STATUS FROM POLICY_MASTER "
                          "WHERE POLICY_ID = 'P1001'").rows == [("Automobile", "Active")]
        assert duck.query("SELECT CUSTOMER_NAME, EMAIL FROM CUSTOMER_360 WHERE CUSTOMER_ID = "
                          "'C1001'").rows == [("NISHA REDDY", "nisha.reddy@example.com")]

    def test_promoting_twice_is_idempotent(self, duck):
        stage_demo_files(duck)
        promote_to_curated(duck, B1)
        again = promote_to_curated(duck, B1)
        assert again["transformed"]["TRN_POLICY"] == 40
        assert count(duck, "SELECT COUNT(*) FROM POLICY_MASTER") == 40

    def test_the_window_covers_the_batch(self, duck):
        stage_demo_files(duck)
        out = promote_to_curated(duck, B1)
        assert out["window"] == validation_window(duck, B1)
        start, end = out["window"]
        assert start <= "2026-07-25" and end >= "2026-08-31"

    def test_nothing_staged_blocks(self, duck):
        check = check_for_promotion(duck, "NO_SUCH_BATCH")
        assert not check.ok and "Nothing is staged" in check.errors[0]
        with pytest.raises(ValueError, match="not promoted"):
            promote_to_curated(duck, "NO_SUCH_BATCH")

    def test_duplicate_keys_block_and_nothing_is_written(self, duck):
        cols, rows = read("STG_POLICY")
        stage(duck, "STG_POLICY", B1, cols, rows + rows[:2])   # staging allows it, with a warning
        check = check_for_promotion(duck, B1)
        assert not check.ok and "P1001" in check.errors[0]
        with pytest.raises(ValueError):
            promote_to_curated(duck, B1)
        assert count(duck, "SELECT COUNT(*) FROM TRN_POLICY") == 0
        assert count(duck, "SELECT COUNT(*) FROM POLICY_MASTER") == 0

    def test_a_failure_part_way_rolls_back_everything(self, duck, monkeypatch):
        stage_demo_files(duck)
        real = duck.execute

        def fail_on_claim_merge(sql, params=None):
            if "MERGE INTO CLAIM_MASTER" in sql:
                raise RuntimeError("warehouse suspended")
            return real(sql, params)

        monkeypatch.setattr(duck, "execute", fail_on_claim_merge)
        with pytest.raises(RuntimeError):
            promote_to_curated(duck, B1)
        monkeypatch.undo()
        assert count(duck, "SELECT COUNT(*) FROM TRN_POLICY") == 0      # 03 undone too
        assert count(duck, "SELECT COUNT(*) FROM POLICY_MASTER") == 0   # so is the first MERGE

    def test_a_claims_only_file_links_to_policies_already_in_curated(self, duck):
        stage_demo_files(duck)
        promote_to_curated(duck, B1)
        cols, rows = read("STG_CLAIM")
        stage(duck, "STG_CLAIM", B2,
              cols, with_column(cols, rows, "LAST_UPDATED_TS", "2026-09-14 09:00:00", n=5))
        check = check_for_promotion(duck, B2)
        assert check.ok and not check.dropped_claims
        out = promote_to_curated(duck, B2)
        assert out["transformed"]["TRN_CLAIM"] == 5 and out["curated"]["CLAIM_MASTER"] == 5

    def test_claims_with_an_unknown_policy_are_reported_before_they_are_dropped(self, duck):
        cols, rows = read("STG_CLAIM")
        stage(duck, "STG_CLAIM", B1, cols, rows[:3])      # no policies anywhere yet
        check = check_for_promotion(duck, B1)
        assert check.ok
        assert check.dropped_claims == ["CL1001", "CL1002", "CL1003"]
        assert "CLM-006" in check.warnings[0]
        out = promote_to_curated(duck, B1)
        assert out["transformed"]["TRN_CLAIM"] == 0
        assert out["dropped_claims"] == ["CL1001", "CL1002", "CL1003"]

    def test_a_policies_only_file_refreshes_customer_totals(self, duck):
        stage_demo_files(duck)
        promote_to_curated(duck, B1)
        cols, rows = read("STG_POLICY")
        rows = with_column(cols, rows, "LAST_UPDATED_TS", "2026-09-14 09:00:00", n=1)
        rows = with_column(cols, rows, "PREMIUM_AMOUNT", "20101.97")          # +1000
        cust = rows[0][1]
        before = duck.query("SELECT TOTAL_PREMIUM FROM CUSTOMER_360 WHERE CUSTOMER_ID = :c",
                            {"c": cust}).rows[0][0]
        stage(duck, "STG_POLICY", B2, cols, rows)
        promote_to_curated(duck, B2)
        after = duck.query("SELECT TOTAL_PREMIUM FROM CUSTOMER_360 WHERE CUSTOMER_ID = :c",
                           {"c": cust}).rows[0][0]
        assert after - before == 1000


class TestWatermarks:
    def mark(self, duck, process):
        return duck.query("SELECT PREVIOUS_WATERMARK, BATCH_HIGH_WATERMARK, LAST_BATCH_ID "
                          "FROM DQ_WATERMARK WHERE PROCESS_NAME = :p", {"p": process}).rows

    def test_one_watermark_per_entity(self, duck):
        stage_demo_files(duck)
        promote_to_curated(duck, B1)
        for p in ("POLICY_CURATED_LOAD", "CLAIM_CURATED_LOAD", "CUSTOMER_CURATED_LOAD"):
            (prev, high, last), = self.mark(duck, p)
            assert prev is None and high is not None and last == B1

    def test_a_new_batch_starts_where_the_last_one_ended(self, duck):
        stage_demo_files(duck)
        promote_to_curated(duck, B1)
        (_, b1_high, _), = self.mark(duck, "CLAIM_CURATED_LOAD")
        cols, rows = read("STG_CLAIM")
        stage(duck, "STG_CLAIM", B2,
              cols, with_column(cols, rows, "LAST_UPDATED_TS", "2026-09-14 09:00:00", n=2))
        promote_to_curated(duck, B2)
        (prev, high, last), = self.mark(duck, "CLAIM_CURATED_LOAD")
        assert (prev, last) == (b1_high, B2) and str(high).startswith("2026-09-14")
        promote_to_curated(duck, B2)                        # reloading keeps the start
        assert self.mark(duck, "CLAIM_CURATED_LOAD")[0][0] == b1_high

    def test_entities_a_batch_did_not_carry_keep_their_watermark(self, duck):
        stage_demo_files(duck)
        promote_to_curated(duck, B1)
        policy_mark = self.mark(duck, "POLICY_CURATED_LOAD")
        cols, rows = read("STG_CLAIM")
        stage(duck, "STG_CLAIM", B2,
              cols, with_column(cols, rows, "LAST_UPDATED_TS", "2026-09-14 09:00:00", n=2))
        promote_to_curated(duck, B2)
        assert self.mark(duck, "POLICY_CURATED_LOAD") == policy_mark

    def test_an_old_style_watermark_is_reset_rather_than_failing_the_batch(self, duck):
        """Before this change PREVIOUS_WATERMARK held the batch's own minimum."""
        stage_demo_files(duck)
        promote_to_curated(duck, B1)
        duck.execute("UPDATE DQ_WATERMARK SET PREVIOUS_WATERMARK = "
                     "(SELECT MIN(RECORD_EFFECTIVE_TS) FROM TRN_POLICY WHERE BATCH_ID = :b) "
                     "WHERE PROCESS_NAME = 'POLICY_CURATED_LOAD'", {"b": B1})
        promote_to_curated(duck, B1)
        assert self.mark(duck, "POLICY_CURATED_LOAD")[0][0] is None


class TestCuratedValidation:
    def promote_and_validate(self, duck, suite, batch):
        eng, pid = suite
        out = promote_to_curated(duck, batch)
        return validate_curated(eng, pid, batch, window=out["window"])

    def test_a_clean_upload_passes_every_curated_validation(self, duck, suite):
        stage_demo_files(duck)
        results = self.promote_and_validate(duck, suite, B1)
        assert set(results) == {"/Source-to-Target", "/Historical", "/Incremental"}
        assert sum(len(r) for r in results.values()) == 13
        assert failing(results) == {}

    def test_a_clean_delta_file_passes(self, duck, suite):
        stage_demo_files(duck)
        self.promote_and_validate(duck, suite, B1)
        cols, rows = read("STG_CLAIM")
        rows = with_column(cols, rows, "LAST_UPDATED_TS", "2026-09-14 09:00:00", n=5)
        rows = with_column(cols, rows, "CLAIM_STATUS", "S")
        stage(duck, "STG_CLAIM", B2, cols, rows)
        assert failing(self.promote_and_validate(duck, suite, B2)) == {}

    def test_the_demo_defects_are_caught_end_to_end(self, duck, suite):
        stage_demo_files(duck)
        promote_to_curated(duck, B1)
        etl_loader.introduce_mismatches(B1, duck)
        eng, pid = suite
        bad = failing(validate_curated(eng, pid, B1, window=validation_window(duck, B1)))
        policy = bad["Policy staging vs curated (mapping rules)"][1]
        claim = bad["Claim staging vs curated (mapping rules)"][1]
        assert ("VALUE_MISMATCH", "P1005", "PREMIUM_AMOUNT") in policy
        assert ("VALUE_MISMATCH", "CL1007", "CLAIM_STATUS") in claim
        assert ("VALUE_MISMATCH", "CL1010", "POLICY_ID") in claim
        assert "Historical claim reconciliation (date range)" in bad

        etl_loader.fix_mismatches(B1, duck)
        assert failing(validate_curated(eng, pid, B1, window=validation_window(duck, B1))) == {}

    def test_a_wrong_mapping_rule_is_caught_even_when_layers_agree(self, duck, suite):
        """Transformation and curated both wrong the same way: only the staging check sees it."""
        stage_demo_files(duck)
        promote_to_curated(duck, B1)
        duck.execute("UPDATE TRN_POLICY SET POLICY_TYPE_DESC = 'Motor' WHERE POLICY_ID = 'P1001'")
        duck.execute("UPDATE POLICY_MASTER SET POLICY_TYPE = 'Motor' WHERE POLICY_ID = 'P1001'")
        eng, pid = suite
        bad = failing(validate_curated(eng, pid, B1, window=validation_window(duck, B1)))
        assert "Policy transformation vs curated" not in bad
        assert bad["Policy staging vs curated (mapping rules)"][1] == [
            ("VALUE_MISMATCH", "P1001", "POLICY_TYPE")]

    def test_old_records_sent_again_fail_the_incremental_check(self, duck, suite):
        stage_demo_files(duck)
        self.promote_and_validate(duck, suite, B1)
        cols, rows = read("STG_POLICY")
        stage(duck, "STG_POLICY", B2, cols, rows[:3])        # same records, same timestamps
        bad = failing(self.promote_and_validate(duck, suite, B2))
        assert set(bad) == {"Incremental watermark boundary"}
        assert {k for _, k, _ in bad["Incremental watermark boundary"][1]} == {
            "P1001", "P1002", "P1003"}

    def test_an_out_of_order_load_fails_the_historical_check(self, duck, suite):
        """A newer version reaches curated, then an older file overwrites it."""
        stage_demo_files(duck)
        self.promote_and_validate(duck, suite, B1)
        cols, rows = read("STG_POLICY")
        newer = with_column(cols, rows, "LAST_UPDATED_TS", "2026-09-18 10:00:00", n=1)
        newer = with_column(cols, newer, "PREMIUM_AMOUNT", "25000.00")
        stage(duck, "STG_POLICY", B2, cols, newer)
        promote_to_curated(duck, B2)
        older = with_column(cols, rows, "LAST_UPDATED_TS", "2026-09-10 10:00:00", n=1)
        stage(duck, "STG_POLICY", B3, cols, older)
        bad = failing(self.promote_and_validate(duck, suite, B3))
        eng, pid = suite
        history = failing(validate_curated(eng, pid, B3, window=("2026-07-01", "2026-12-31")))
        assert ("VALUE_MISMATCH", "P1001", "PREMIUM_AMOUNT") in \
            history["Historical policy reconciliation (date range)"][1]
        assert "Incremental watermark boundary" in bad

    def test_validating_a_batch_whose_watermark_moved_on_says_so(self, duck, suite):
        stage_demo_files(duck)
        self.promote_and_validate(duck, suite, B1)
        cols, rows = read("STG_POLICY")
        stage(duck, "STG_POLICY", B2,
              cols, with_column(cols, rows, "LAST_UPDATED_TS", "2026-09-14 09:00:00", n=1))
        promote_to_curated(duck, B2)
        eng, pid = suite
        bad = failing(validate_curated(eng, pid, B1, window=validation_window(duck, B1)))
        assert bad["Incremental watermark boundary"][1] == [
            ("RULE_VIOLATION", B1, "LAST_BATCH_ID")]


class TestRecordCounts:
    def test_totals_and_batch_counts_per_table(self, duck):
        stage_demo_files(duck)
        promote_to_curated(duck, B1)
        cols, rows = read("STG_CLAIM")
        stage(duck, "STG_CLAIM", B2,
              cols, with_column(cols, rows, "LAST_UPDATED_TS", "2026-09-14 09:00:00", n=5))
        promote_to_curated(duck, B2)
        counts = {c["table"]: (c["layer"], c["total"], c["batch"])
                  for c in etl_loader.record_counts(duck, B2)}
        assert counts["STG_CLAIM"] == ("Staging", 55, 5)
        assert counts["TRN_POLICY"] == ("Transformation", 40, 0)
        assert counts["CLAIM_MASTER"] == ("Curated", 50, 5)   # the delta updated existing keys
        assert len(counts) == 9
