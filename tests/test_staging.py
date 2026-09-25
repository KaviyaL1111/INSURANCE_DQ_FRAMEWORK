"""Loading a flat file into the staging tables."""
from __future__ import annotations

import os
import re

import pytest

from src.staging import (STAGING_TABLES, check_for_staging, guess_staging_table,
                         load_to_staging, read_file_as_text)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLAIM_COLS = STAGING_TABLES["STG_CLAIM"][1]


@pytest.fixture
def staging_db(repo):
    """The repository schema plus the real STG_* DDL from sql/01."""
    ddl = open(os.path.join(ROOT, "sql", "01_ddl_create_all_tables.sql")).read()
    for stmt in re.findall(r"CREATE TABLE IF NOT EXISTS STG_\w+ \(.*?\);", ddl, re.S):
        repo.db.execute(stmt)
    repo.db.commit()
    return repo.db


def claim(cid="CL1", pid="P1", cust="C1", date="2025-01-09", amount="100.50",
          approved="80", status="A", incident="Fire", ts="2026-08-01 10:00:00"):
    return (cid, pid, cust, date, amount, approved, status, incident, ts)


def count(db, table, batch):
    return db.query(f"SELECT COUNT(*) FROM {table} WHERE BATCH_ID = :b", {"b": batch}).rows[0][0]


class TestChecks:
    def test_a_clean_file_passes(self):
        check = check_for_staging("STG_CLAIM", CLAIM_COLS, [claim(), claim("CL2")])
        assert check.ok and not check.warnings

    def test_missing_columns_block_the_load(self):
        check = check_for_staging("STG_CLAIM", CLAIM_COLS[:-2], [claim()[:-2]])
        assert not check.ok
        assert check.missing_columns == ["INCIDENT_TYPE", "LAST_UPDATED_TS"]

    def test_column_names_match_case_insensitively_and_extras_are_ignored(self):
        cols = [c.lower() for c in CLAIM_COLS] + ["NOTES"]
        check = check_for_staging("STG_CLAIM", cols, [claim() + ("x",)])
        assert check.ok
        assert check.extra_columns == ["NOTES"] and "Ignored" in check.warnings[0]

    @pytest.mark.parametrize("row,problem", [
        (claim(cid=""), "CLAIM_ID is empty"),
        (claim(pid=None), "POLICY_ID is empty"),
        (claim(amount="12k"), "CLAIM_AMOUNT is not a number"),
        (claim(date="31/12/2024"), "CLAIM_DATE is not a YYYY-MM-DD date"),
        (claim(date="2024-02-30"), "CLAIM_DATE is not a YYYY-MM-DD date"),
        (claim(ts="yesterday"), "LAST_UPDATED_TS is not"),
        (claim(cid="X" * 21), "CLAIM_ID is longer than 20"),
    ])
    def test_values_the_database_would_reject_block_the_load(self, row, problem):
        check = check_for_staging("STG_CLAIM", CLAIM_COLS, [claim("OK"), row])
        assert not check.ok
        assert any(problem in e and "row 2" in e for e in check.errors), check.errors

    def test_duplicates_are_a_warning_not_a_blocker(self):
        check = check_for_staging("STG_CLAIM", CLAIM_COLS, [claim(), claim()])
        assert check.ok
        assert "duplicate CLAIM_ID" in check.warnings[0]

    def test_empty_file_blocks(self):
        assert not check_for_staging("STG_CLAIM", CLAIM_COLS, []).ok

    @pytest.mark.parametrize("name,table", [
        ("CLAIMS", "STG_CLAIM"), ("claim_2026", "STG_CLAIM"), ("POLICY", "STG_POLICY"),
        ("policies", "STG_POLICY"), ("CUSTOMER", "STG_CUSTOMER"), ("AGENTS", None),
    ])
    def test_guess_staging_table(self, name, table):
        assert guess_staging_table(name) == table


class TestLoad:
    def test_rows_land_in_staging_with_the_batch(self, staging_db):
        out = load_to_staging(staging_db, "STG_CLAIM", CLAIM_COLS,
                              [claim(), claim("CL2", amount="")], "B1", "claims.csv")
        assert out == {"table": "STG_CLAIM", "batch_id": "B1", "deleted": 0,
                       "inserted": 2, "staged_now": 2}
        rows = staging_db.query("SELECT CLAIM_ID, CLAIM_AMOUNT, CLAIM_DATE FROM STG_CLAIM "
                                "ORDER BY CLAIM_ID").rows
        assert rows == [("CL1", 100.5, "2025-01-09"), ("CL2", None, "2025-01-09")]

    def test_reloading_replaces_the_batch_instead_of_doubling_it(self, staging_db):
        load_to_staging(staging_db, "STG_CLAIM", CLAIM_COLS, [claim(), claim("CL2")], "B1")
        out = load_to_staging(staging_db, "STG_CLAIM", CLAIM_COLS, [claim("CL9")], "B1")
        assert out["deleted"] == 2 and out["staged_now"] == 1

    def test_append_keeps_existing_rows(self, staging_db):
        load_to_staging(staging_db, "STG_CLAIM", CLAIM_COLS, [claim()], "B1")
        out = load_to_staging(staging_db, "STG_CLAIM", CLAIM_COLS, [claim("CL2")], "B1",
                              replace=False)
        assert out["staged_now"] == 2

    def test_other_batches_are_untouched(self, staging_db):
        load_to_staging(staging_db, "STG_CLAIM", CLAIM_COLS, [claim()], "OLD")
        load_to_staging(staging_db, "STG_CLAIM", CLAIM_COLS, [claim("CL2")], "NEW")
        assert count(staging_db, "STG_CLAIM", "OLD") == 1

    def test_a_bad_file_writes_nothing(self, staging_db):
        load_to_staging(staging_db, "STG_CLAIM", CLAIM_COLS, [claim()], "B1")
        with pytest.raises(ValueError, match="CLAIM_AMOUNT is not a number"):
            load_to_staging(staging_db, "STG_CLAIM", CLAIM_COLS,
                            [claim("CL2"), claim("CL3", amount="abc")], "B1")
        assert count(staging_db, "STG_CLAIM", "B1") == 1  # the earlier load is intact

    def test_a_database_failure_rolls_back(self, staging_db, monkeypatch):
        load_to_staging(staging_db, "STG_CLAIM", CLAIM_COLS, [claim()], "B1")
        real = staging_db.execute

        def fail_on_insert(sql, params=None):
            if sql.startswith("INSERT INTO STG_CLAIM"):
                raise RuntimeError("connection lost")
            return real(sql, params)

        monkeypatch.setattr(staging_db, "execute", fail_on_insert)
        with pytest.raises(RuntimeError):
            load_to_staging(staging_db, "STG_CLAIM", CLAIM_COLS, [claim("CL2")], "B1")
        monkeypatch.undo()
        assert staging_db.query("SELECT CLAIM_ID FROM STG_CLAIM").rows == [("CL1",)]

    def test_large_files_are_inserted_in_chunks(self, staging_db):
        rows = [claim(f"CL{i}") for i in range(1000)]
        assert load_to_staging(staging_db, "STG_CLAIM", CLAIM_COLS, rows, "B1")["inserted"] == 1000
        assert count(staging_db, "STG_CLAIM", "B1") == 1000

    def test_batch_metadata_tracks_everything_staged(self, staging_db):
        cust_cols = STAGING_TABLES["STG_CUSTOMER"][1]
        load_to_staging(staging_db, "STG_CLAIM", CLAIM_COLS, [claim(), claim("CL2")], "B1",
                        "claims.csv")
        load_to_staging(staging_db, "STG_CUSTOMER", cust_cols,
                        [("C1", "Asha", "a@x.com", "+911", "Pune", "")], "B1", "customers.csv")
        meta = staging_db.query("SELECT SOURCE_ROWS, SOURCE_FILE, LOAD_STATUS "
                                "FROM DQ_BATCH_METADATA WHERE BATCH_ID = 'B1'").rows
        assert meta == [(3, "claims.csv, customers.csv", "LOADED")]


class TestFromFile:
    def test_phone_numbers_and_ids_stay_text(self, tmp_path, staging_db):
        (tmp_path / "customer.csv").write_text(
            "CUSTOMER_ID,CUSTOMER_NAME,EMAIL,PHONE_NUMBER,CITY,LAST_UPDATED_TS\n"
            "00123,Asha,a@x.com,+917107420369,Pune,2026-08-17 04:52:00\n")
        cols, rows = read_file_as_text(str(tmp_path / "customer.csv"))
        load_to_staging(staging_db, "STG_CUSTOMER", cols, rows, "B1")
        assert staging_db.query("SELECT CUSTOMER_ID, PHONE_NUMBER FROM STG_CUSTOMER").rows == \
            [("00123", "+917107420369")]

    def test_the_bundled_demo_files_load_cleanly(self, staging_db):
        for table, (csv_name, _) in STAGING_TABLES.items():
            cols, rows = read_file_as_text(os.path.join(ROOT, "data", csv_name))
            out = load_to_staging(staging_db, table, cols, rows, "DEMO", csv_name)
            assert out["staged_now"] == len(rows) > 0

    def test_staged_file_reconciles_with_the_flat_file(self, staging_db):
        """End to end: load claims.csv, then the seeded file-vs-staging comparison passes."""
        from src.comparator import compare_result_sets
        from src.connectors import get_connector
        cols, rows = read_file_as_text(os.path.join(ROOT, "data", "claims.csv"))
        load_to_staging(staging_db, "STG_CLAIM", cols, rows, "B1")
        sel = ", ".join(CLAIM_COLS)
        tgt = staging_db.query(f"SELECT {sel} FROM STG_CLAIM WHERE BATCH_ID = 'B1'")
        with get_connector("flatfile") as ff:
            src = ff.query(f"SELECT {sel} FROM CLAIMS")
        report = compare_result_sets(src.columns, src.rows, tgt.columns, tgt.rows,
                                     key_columns=["CLAIM_ID"])
        assert report.status == "PASS", report.failures[:3]
