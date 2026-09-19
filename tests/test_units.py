"""Pure-logic tests — no database required."""
from decimal import Decimal

import pytest

from src.comparator import compare_result_sets, compare_row_counts, values_equal
from src.connectors.base import extract_params, split_statements
from src.history import day_end, day_start
from src.repository import TestCase


class TestStatementSplitting:
    def test_semicolon_inside_a_literal_does_not_split(self):
        assert split_statements("SELECT ';' AS A; SELECT 2") == ["SELECT ';' AS A", "SELECT 2"]

    def test_comments_are_stripped_and_never_split(self):
        stmts = split_statements("-- a; comment\nSELECT 1;\n/* b; block */ SELECT 2;")
        assert stmts == ["SELECT 1", "SELECT 2"]

    def test_escaped_quotes(self):
        assert split_statements("SELECT 'it''s fine; really' AS A") == \
            ["SELECT 'it''s fine; really' AS A"]

    def test_trailing_semicolon_makes_no_empty_statement(self):
        assert split_statements("SELECT 1;;\n\n") == ["SELECT 1"]


class TestParamExtraction:
    def test_finds_named_params(self):
        assert extract_params("SELECT * FROM t WHERE a = :batch_id AND b = :other") == \
            ["batch_id", "other"]

    def test_ignores_snowflake_cast_operator(self):
        assert extract_params("SELECT x::VARCHAR, y::NUMBER FROM t") == []

    def test_ignores_params_inside_literals_and_comments(self):
        assert extract_params("SELECT ':nope' -- :also_nope\nFROM t WHERE a = :yes") == ["yes"]

    def test_deduplicates_preserving_order(self):
        assert extract_params("SELECT :b, :a, :b FROM t") == ["b", "a"]


class TestValueEquality:
    @pytest.mark.parametrize("a,b", [
        (Decimal("12000.00"), 12000.0),      # Snowflake Decimal vs MSSQL float
        ("12000.00", Decimal("12000")),      # numeric string vs number
        ("Approved  ", "Approved"),          # CHAR padding
        (None, None),
        (1, Decimal("1.0")),
    ])
    def test_equivalent_across_drivers(self, a, b):
        assert values_equal(a, b)

    @pytest.mark.parametrize("a,b", [
        (Decimal("12000"), Decimal("10000")),
        (None, "x"),
        ("Approved", "Pending"),
    ])
    def test_genuinely_different(self, a, b):
        assert not values_equal(a, b)

    def test_tolerance(self):
        assert values_equal(Decimal("100.004"), Decimal("100.001"), tolerance=0.01)
        assert not values_equal(Decimal("100.05"), Decimal("100.00"), tolerance=0.01)


class TestComparison:
    def _sets(self):
        cols = ["POLICY_ID", "PREMIUM_AMOUNT", "POLICY_TYPE"]
        src = [("P1", Decimal("100"), "Auto"), ("P2", Decimal("200"), "Home"),
               ("P3", Decimal("300"), "Life")]
        tgt = [("P1", Decimal("100"), "Auto"), ("P2", Decimal("999"), "Home"),
               ("P4", Decimal("400"), "Health")]
        return cols, src, tgt

    def test_reports_every_defect_class(self):
        cols, src, tgt = self._sets()
        r = compare_result_sets(cols, src, cols, tgt, key_columns=["POLICY_ID"])
        kinds = {f.failure_type for f in r.failures}
        assert kinds == {"VALUE_MISMATCH", "MISSING_IN_TARGET", "EXTRA_IN_TARGET"}
        assert r.status == "FAIL"
        assert r.matched_record_count == 1          # only P1 is clean

    def test_mismatch_carries_expected_and_actual(self):
        cols, src, tgt = self._sets()
        r = compare_result_sets(cols, src, cols, tgt, key_columns=["POLICY_ID"])
        mismatch = next(f for f in r.failures if f.failure_type == "VALUE_MISMATCH")
        assert mismatch.business_key == "P2"
        assert mismatch.column_name == "PREMIUM_AMOUNT"
        assert mismatch.expected_value == "200"
        assert mismatch.actual_value == "999"

    def test_identical_sets_pass(self):
        cols, src, _ = self._sets()
        r = compare_result_sets(cols, src, cols, list(src), key_columns=["POLICY_ID"])
        assert r.status == "PASS" and r.failed_record_count == 0
        assert r.matched_record_count == 3

    def test_only_named_columns_are_compared(self):
        cols, src, tgt = self._sets()
        r = compare_result_sets(cols, src, cols, tgt, key_columns=["POLICY_ID"],
                                compare_columns=["POLICY_TYPE"])
        assert not any(f.failure_type == "VALUE_MISMATCH" for f in r.failures)

    def test_composite_key(self):
        cols = ["A", "B", "V"]
        r = compare_result_sets(cols, [("x", "1", 10)], cols, [("x", "1", 99)],
                                key_columns=["A", "B"])
        assert r.failures[0].business_key == "x | 1"

    def test_duplicate_key_is_flagged(self):
        cols = ["K", "V"]
        r = compare_result_sets(cols, [("k", 1), ("k", 2)], cols, [("k", 1)], key_columns=["K"])
        assert any(f.failure_type == "DUPLICATE_KEY" for f in r.failures)

    def test_missing_key_column_raises_a_useful_error(self):
        with pytest.raises(ValueError, match="Key column"):
            compare_result_sets(["A"], [("x",)], ["B"], [("x",)], key_columns=["ID"])

    def test_row_count_comparison(self):
        assert compare_row_counts(50, 50).status == "PASS"
        bad = compare_row_counts(50, 49)
        assert bad.status == "FAIL" and bad.failures[0].expected_value == "50"


class TestDateWindow:
    def test_end_date_is_widened_to_end_of_day(self):
        """A start==end filter must return that whole day, not zero rows."""
        s, e = day_start("2026-09-01"), day_end("2026-09-01")
        assert s.hour == 0 and s.minute == 0
        assert e.hour == 23 and e.minute == 59
        assert s < e

    def test_explicit_time_is_respected(self):
        from datetime import datetime
        assert day_end(datetime(2026, 9, 1, 10, 30)).hour == 10


class TestTestCaseValidation:
    def test_compare_test_requires_key_columns(self):
        tc = TestCase(test_name="x", test_type="SOURCE_TARGET_COMPARE",
                      source_sql="SELECT 1", target_sql="SELECT 1")
        assert any("Key column" in p for p in tc.validate())

    def test_rule_test_requires_sql(self):
        assert any("Validation SQL" in p for p in TestCase(test_name="x").validate())

    def test_name_is_required(self):
        assert any("Test name" in p for p in TestCase(validation_sql="SELECT 1").validate())

    def test_valid_case_has_no_problems(self):
        tc = TestCase(test_name="x", test_type="SOURCE_TARGET_COMPARE", key_columns="ID",
                      source_sql="SELECT 1", target_sql="SELECT 1")
        assert tc.validate() == []

    def test_required_params_span_all_sql_fields(self):
        tc = TestCase(test_name="x", test_type="SOURCE_TARGET_COMPARE", key_columns="ID",
                      source_sql="SELECT :a", target_sql="SELECT :b")
        assert tc.required_params() == ["a", "b"]
