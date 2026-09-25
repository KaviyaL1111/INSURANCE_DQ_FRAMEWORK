"""Flat-file connectivity: a folder of files queried like a database."""
from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal

import pytest

from src.comparator import compare_result_sets, values_equal
from src.connectors import get_connector
from src.connectors.base import ConnectionProfile
from src.connectors.flatfile_connector import (FlatFileConnector, safe_filename,
                                               table_name_for)
from src.repository import TestCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _connector(path, **options) -> FlatFileConnector:
    return FlatFileConnector(ConnectionProfile(name="flatfile", kind="flatfile",
                                               options={"path": str(path), **options}))


@pytest.fixture
def files(tmp_path):
    (tmp_path / "policies.csv").write_text(
        "POLICY_ID,PREMIUM_AMOUNT,ISSUE_DATE,STATUS\n"
        "P1001,19101.97,2024-01-15,Active\n"
        "P1002,8831.25,2024-02-01,Inactive\n"
        "P1003,,2024-03-10,Active\n")
    (tmp_path / "claims feed.txt").write_text(
        "CLAIM_ID;POLICY_ID;AMOUNT\nCL1;P1001;100.5\nCL2;P1002;200\n")
    (tmp_path / "agents.tsv").write_text("AGENT_ID\tNAME\nA1\tAsha\n")
    (tmp_path / "2026 q1.psv").write_text("K|V\n1|x\n")
    (tmp_path / "readme.md").write_text("not data")
    return tmp_path


class TestLoading:
    def test_every_supported_file_becomes_a_table(self, files):
        with _connector(files) as ff:
            names = {t["table"] for t in ff.describe()}
        assert names == {"POLICIES", "CLAIMS_FEED", "AGENTS", "T_2026_Q1"}

    def test_delimiter_is_sniffed_or_taken_from_the_extension(self, files):
        with _connector(files) as ff:
            assert ff.query("SELECT AMOUNT FROM CLAIMS_FEED ORDER BY CLAIM_ID").rows == \
                [(100.5,), (200.0,)]
            assert ff.query("SELECT NAME FROM AGENTS").rows == [("Asha",)]
            assert ff.query("SELECT V FROM T_2026_Q1").rows == [("x",)]

    def test_blank_cells_are_null(self, files):
        with _connector(files) as ff:
            res = ff.query("SELECT POLICY_ID FROM POLICIES WHERE PREMIUM_AMOUNT IS NULL")
        assert res.rows == [("P1003",)]

    def test_all_text_keeps_leading_zeros(self, tmp_path):
        (tmp_path / "codes.csv").write_text("CODE\n00123\n")
        with _connector(tmp_path, all_text=True) as ff:
            assert ff.query("SELECT CODE FROM CODES").rows == [("00123",)]

    def test_headerless_files_get_numbered_columns(self, tmp_path):
        (tmp_path / "raw.csv").write_text("a,1\nb,2\n")
        with _connector(tmp_path, header=False) as ff:
            assert ff.query("SELECT COLUMN_1 FROM RAW WHERE COLUMN_2 = 2").rows == [("b",)]

    def test_each_excel_sheet_becomes_a_table(self, tmp_path):
        pytest.importorskip("openpyxl")
        import pandas as pd
        with pd.ExcelWriter(tmp_path / "book.xlsx") as xw:
            pd.DataFrame({"ID": ["A"]}).to_excel(xw, sheet_name="Motor", index=False)
            pd.DataFrame({"ID": ["B", "C"]}).to_excel(xw, sheet_name="Home", index=False)
        with _connector(tmp_path) as ff:
            assert {t["table"]: t["rows"] for t in ff.describe()} == \
                {"BOOK_MOTOR": 1, "BOOK_HOME": 2}

    def test_an_unreadable_file_does_not_hide_the_others(self, tmp_path):
        (tmp_path / "good.csv").write_text("A\n1\n")
        (tmp_path / "bad.json").write_text("{not json")
        with _connector(tmp_path) as ff:
            assert [t["table"] for t in ff.describe()] == ["GOOD"]
            assert ff.load_errors[0]["file"] == "bad.json"

    def test_nested_json_is_stored_as_json_text(self, tmp_path):
        (tmp_path / "nested.json").write_text('[{"ID": 1, "TAGS": ["a", "b"], "ADDR": {"CITY": "Pune"}}]')
        (tmp_path / "ok.csv").write_text("A\n1\n")
        with _connector(tmp_path) as ff:
            assert ff.query("SELECT TAGS, ADDR FROM NESTED").rows == \
                [('["a", "b"]', '{"CITY": "Pune"}')]
            assert ff.load_errors == []

    def test_jsonl_and_parquet(self, tmp_path):
        pytest.importorskip("pyarrow")
        import pandas as pd
        (tmp_path / "events.jsonl").write_text('{"ID": 1}\n{"ID": 2}\n')
        pd.DataFrame({"ID": [1], "TS": pd.to_datetime(["2024-01-01 10:00"])}) \
            .to_parquet(tmp_path / "snap.parquet")
        with _connector(tmp_path) as ff:
            assert ff.query("SELECT COUNT(*) FROM EVENTS").rows == [(2,)]
            assert ff.query("SELECT TS FROM SNAP").rows == [("2024-01-01 10:00:00",)]

    def test_non_utf8_files_fall_back_to_windows_encoding(self, tmp_path):
        (tmp_path / "names.csv").write_bytes("ID,NAME\n1,José\n".encode("cp1252"))
        (tmp_path / "bom.csv").write_bytes("ID,NAME\n1,Asha\n".encode("utf-8-sig"))
        with _connector(tmp_path) as ff:
            assert ff.query("SELECT NAME FROM NAMES").rows == [("José",)]
            assert ff.query("SELECT ID FROM BOM").rows == [(1,)]

    def test_only_empty_cells_are_null(self, tmp_path):
        """'NA' or 'null' in a file is data a DQ check must see, not a missing value."""
        (tmp_path / "t.csv").write_text("K,V\n1,NA\n2,null\n3,\n")
        with _connector(tmp_path) as ff:
            assert ff.query("SELECT V FROM T ORDER BY K").rows == [("NA",), ("null",), (None,)]

    def test_quoted_delimiters_and_newlines(self, tmp_path):
        (tmp_path / "q.csv").write_text('ID,NOTE\n1,"has, comma"\n2,"two\nlines"\n')
        with _connector(tmp_path) as ff:
            assert ff.query("SELECT NOTE FROM Q ORDER BY ID").rows == \
                [("has, comma",), ("two\nlines",)]

    def test_decimal_comma_files(self, tmp_path):
        (tmp_path / "eu.csv").write_text("ID;AMOUNT\n1;1234,50\n")
        with _connector(tmp_path, decimal=",") as ff:
            assert ff.query("SELECT AMOUNT FROM EU").rows == [(1234.5,)]

    def test_empty_and_header_only_files(self, tmp_path):
        (tmp_path / "empty.csv").write_text("")
        (tmp_path / "header.csv").write_text("A,B\n")
        with _connector(tmp_path) as ff:
            assert {t["table"]: t["rows"] for t in ff.describe()} == {"HEADER": 0}
            assert "empty" in ff.load_errors[0]["error"]

    def test_duplicate_and_padded_headers(self, tmp_path):
        (tmp_path / "d.csv").write_text(" ID , Name ,name\n1,a,b\n")
        with _connector(tmp_path) as ff:
            assert ff.describe()[0]["columns"] == ["ID", "Name", "name_2"]

    def test_concurrent_queries_are_safe(self, files):
        from concurrent.futures import ThreadPoolExecutor
        ff = _connector(files)
        ff.ensure_loaded()
        with ThreadPoolExecutor(8) as pool:
            counts = list(pool.map(
                lambda _: ff.query("SELECT COUNT(*) FROM POLICIES").rows[0][0], range(200)))
        ff.close()
        assert set(counts) == {3}

    def test_a_single_file_path_works_too(self, files):
        with _connector(files / "policies.csv") as ff:
            assert [t["table"] for t in ff.describe()] == ["POLICIES"]

    def test_refresh_picks_up_new_files(self, tmp_path):
        (tmp_path / "a.csv").write_text("X\n1\n")
        ff = _connector(tmp_path)
        assert len(ff.describe()) == 1
        (tmp_path / "b.csv").write_text("Y\n2\n")
        ff.refresh()
        assert len(ff.describe()) == 2
        ff.close()

    def test_missing_folder_is_a_clear_error(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="does not exist"):
            _connector(tmp_path / "nope").describe()


class TestBinding:
    def test_named_params_bind_natively(self, files):
        with _connector(files) as ff:
            res = ff.query("SELECT POLICY_ID FROM POLICIES WHERE STATUS = :s ORDER BY 1",
                           {"s": "Active", "unused": 1})
        assert res.rows == [("P1001",), ("P1003",)]

    def test_dates_and_decimals_are_adapted(self, files):
        with _connector(files) as ff:
            res = ff.query("SELECT POLICY_ID FROM POLICIES "
                           "WHERE ISSUE_DATE = :d AND PREMIUM_AMOUNT > :p",
                           {"d": date(2024, 1, 15), "p": Decimal("100")})
        assert res.rows == [("P1001",)]

    def test_missing_param_is_reported(self, files):
        with _connector(files) as ff, pytest.raises(KeyError, match="batch_id"):
            ff.query("SELECT * FROM POLICIES WHERE STATUS = :batch_id")


class TestHelpers:
    @pytest.mark.parametrize("path,suffix,expected", [
        ("claims.csv", "", "CLAIMS"),
        ("/x/Motor Book-2026.xlsx", "Sheet 1", "MOTOR_BOOK_2026_SHEET_1"),
        ("9lives.csv", "", "T_9LIVES"),
    ])
    def test_table_names(self, path, suffix, expected):
        assert table_name_for(path, suffix) == expected

    def test_upload_names_cannot_escape_the_folder(self):
        assert safe_filename("../../etc/passwd.csv") == "passwd.csv"
        assert safe_filename("C:\\temp\\My File (1).CSV") == "My File _1.csv"
        with pytest.raises(ValueError, match="not a supported"):
            safe_filename("evil.sh")


class TestComparison:
    def test_iso_text_equals_real_dates(self):
        assert values_equal("2024-10-31", date(2024, 10, 31))
        assert values_equal("2026-08-02 22:04:00", datetime(2026, 8, 2, 22, 4))
        assert not values_equal("2024-10-31", date(2024, 11, 1))

    def test_file_reconciles_against_a_database_table(self, files, db):
        db.execute("CREATE TABLE TGT (POLICY_ID TEXT, PREMIUM_AMOUNT REAL, ISSUE_DATE TEXT)")
        db.execute("INSERT INTO TGT VALUES ('P1001', 19101.97, '2024-01-15'), "
                   "('P1002', 9000, '2024-02-01')")
        with _connector(files) as ff:
            src = ff.query("SELECT POLICY_ID, PREMIUM_AMOUNT, ISSUE_DATE FROM POLICIES")
        tgt = db.query("SELECT * FROM TGT")
        report = compare_result_sets(src.columns, src.rows, tgt.columns, tgt.rows,
                                     key_columns=["POLICY_ID"])
        kinds = sorted((f.business_key, f.failure_type) for f in report.failures)
        assert kinds == [("P1002", "VALUE_MISMATCH"), ("P1003", "MISSING_IN_TARGET")]


class TestProfile:
    def test_flatfile_profile_defaults_to_the_bundled_data_folder(self, monkeypatch):
        monkeypatch.delenv("FLATFILE_DIR", raising=False)
        conn = get_connector("flatfile")
        try:
            assert {t["table"] for t in conn.describe()} >= {"CLAIMS", "CUSTOMERS", "POLICIES"}
        finally:
            conn.close()

    def test_flatfile_is_never_offered_as_the_repository(self, monkeypatch):
        from src import config
        monkeypatch.setattr(config, "available_connections", lambda: ["snowflake", "flatfile"])
        assert config.repository_connections() == ["snowflake"]

    def test_engine_runs_a_file_vs_table_test_case(self, engine, repo, project, files):
        engine._connectors["flatfile"] = _connector(files)
        repo.db.execute("CREATE TABLE STG_AGENT (AGENT_ID TEXT, NAME TEXT)")
        repo.db.execute("INSERT INTO STG_AGENT VALUES ('A1', 'Asha')")
        folder = repo.create_folder(project["PROJECT_ID"], "Files")
        tc = repo.create_test_case(TestCase(
            project_id=project["PROJECT_ID"], folder_id=folder["FOLDER_ID"],
            test_name="agents file vs stage", test_type="SOURCE_TARGET_COMPARE",
            source_connection="flatfile", target_connection="sqlite",
            source_sql="SELECT * FROM AGENTS", target_sql="SELECT * FROM STG_AGENT",
            key_columns="AGENT_ID"))
        result = engine.run_test_case(tc.test_case_id)
        assert result.status == "PASS", result.error_message
        assert result.matched_record_count == 1
