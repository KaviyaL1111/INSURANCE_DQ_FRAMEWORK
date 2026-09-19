"""
Parameter binding for the real drivers.

The SQLite connector used elsewhere in the suite passes ``:named`` SQL
straight through, so it never exercises the Snowflake/MSSQL translation.
These tests drive that translation directly — no database needed.
"""
import re

import pytest

from src.connectors.base import ConnectionProfile
from src.connectors.mssql_connector import MSSQLConnector
from src.connectors.snowflake_connector import SnowflakeConnector


def snowflake():
    c = SnowflakeConnector.__new__(SnowflakeConnector)
    c.profile = ConnectionProfile("snowflake", "snowflake", {})
    c._conn = None
    return c


def pymssql():
    c = MSSQLConnector.__new__(MSSQLConnector)
    c.profile = ConnectionProfile("mssql", "mssql", {})
    c._conn = object()          # pretend we are connected
    c._driver = "pymssql"
    return c


def pyodbc():
    c = pymssql()
    c._driver = "pyodbc"
    return c


class TestSnowflakeBinding:
    def test_simple_substitution(self):
        sql, args = snowflake().bind("SELECT * FROM t WHERE a = :x", {"x": 1})
        assert sql == "SELECT * FROM t WHERE a = %(x)s"
        assert args == {"x": 1}

    def test_literals_before_a_parameter_do_not_shift_it(self):
        """
        Regression: the masking pass used to change the string's length, so
        offsets computed on the masked copy sliced the original in the wrong
        place and produced SQL like `FROM%(batch_id)sCY`.
        """
        sql = ("SELECT 'POLICY_ID' AS COLUMN_NAME, 'a long literal here' AS X "
               "FROM STG_POLICY WHERE BATCH_ID = :batch_id")
        bound, _ = snowflake().bind(sql, {"batch_id": "B1"})
        assert bound == ("SELECT 'POLICY_ID' AS COLUMN_NAME, 'a long literal here' AS X "
                         "FROM STG_POLICY WHERE BATCH_ID = %(batch_id)s")

    def test_comments_before_a_parameter_do_not_shift_it(self):
        sql = "SELECT 1 -- a comment mentioning :ghost\nFROM t WHERE a = :real"
        bound, args = snowflake().bind(sql, {"real": 1})
        assert bound == "SELECT 1 -- a comment mentioning :ghost\nFROM t WHERE a = %(real)s"
        assert args == {"real": 1}

    def test_block_comment_before_a_parameter(self):
        sql = "/* :ghost and some padding */ SELECT * FROM t WHERE a = :real"
        bound, _ = snowflake().bind(sql, {"real": 1})
        assert bound.endswith("WHERE a = %(real)s")
        assert bound.startswith("/* :ghost and some padding */")

    def test_parameter_inside_a_literal_is_left_alone(self):
        sql, args = snowflake().bind("SELECT ':nope' AS A, :yes AS B", {"yes": 1})
        assert sql == "SELECT ':nope' AS A, %(yes)s AS B"
        assert args == {"yes": 1}

    def test_cast_operator_is_not_a_parameter(self):
        sql, args = snowflake().bind("SELECT x::VARCHAR FROM t WHERE a = :p", {"p": 1})
        assert sql == "SELECT x::VARCHAR FROM t WHERE a = %(p)s"

    def test_repeated_parameter_binds_once(self):
        sql, args = snowflake().bind("SELECT :b, :b, :b FROM t", {"b": "x"})
        assert sql == "SELECT %(b)s, %(b)s, %(b)s FROM t"
        assert args == {"b": "x"}

    def test_unused_values_are_not_passed_to_the_driver(self):
        _, args = snowflake().bind("SELECT :a FROM t", {"a": 1, "unused": 2})
        assert args == {"a": 1}

    def test_missing_value_names_the_parameter(self):
        with pytest.raises(KeyError, match="batch_id"):
            snowflake().bind("SELECT * FROM t WHERE a = :batch_id", {})

    def test_no_parameters_passes_none(self):
        sql, args = snowflake().bind("SELECT 1", {})
        assert (sql, args) == ("SELECT 1", None)


class TestMSSQLBinding:
    def test_pymssql_uses_pyformat(self):
        sql, args = pymssql().bind("SELECT * FROM t WHERE a = :x", {"x": 1})
        assert sql == "SELECT * FROM t WHERE a = %(x)s"
        assert args == {"x": 1}

    def test_pyodbc_uses_positional_qmarks_in_order(self):
        sql, args = pyodbc().bind("SELECT * FROM t WHERE a = :a AND b = :b", {"a": 1, "b": 2})
        assert sql == "SELECT * FROM t WHERE a = ? AND b = ?"
        assert args == (1, 2)

    def test_pyodbc_repeats_the_value_for_a_repeated_parameter(self):
        sql, args = pyodbc().bind("SELECT :p, :p FROM t", {"p": "x"})
        assert sql == "SELECT ?, ? FROM t"
        assert args == ("x", "x")

    def test_pyodbc_leaves_literals_intact(self):
        sql, args = pyodbc().bind("SELECT ':nope', :yes FROM t", {"yes": 1})
        assert sql == "SELECT ':nope', ? FROM t"
        assert args == (1,)


class TestSeededCatalogBinds:
    """Every shipped test case must bind cleanly against the real drivers."""

    @pytest.fixture(scope="class")
    def seeded_sql(self):
        from src.seed import load_seed
        out = []
        for entry in load_seed()["test_cases"]:
            for field in ("validation_sql", "source_sql", "target_sql"):
                if entry.get(field):
                    out.append((entry["name"], field, entry[field]))
        return out

    def test_every_statement_round_trips(self, seeded_sql):
        """
        Bind each statement, then turn %(name)s back into :name. The result
        must be byte-identical to what we started with — which is only true
        if nothing outside the parameters was disturbed.
        """
        conn = snowflake()
        for name, field, sql in seeded_sql:
            from src.connectors.base import extract_params
            params = {p: "v" for p in extract_params(sql)}
            params.setdefault("batch_id", "B")
            bound, _ = conn.bind(sql, params)
            restored = re.sub(r"%\((\w+)\)s", r":\1", bound)
            assert restored == sql, f"{name} / {field} was corrupted by binding"

    def test_no_statement_is_missing_a_parameter(self, seeded_sql):
        from src.connectors.base import extract_params
        allowed = {"batch_id", "run_date", "run_ts", "history_start", "history_end",
                   "previous_watermark", "batch_high_watermark", "test_id"}
        for name, field, sql in seeded_sql:
            unknown = set(extract_params(sql)) - allowed
            assert not unknown, f"{name} / {field} uses undeclared parameter(s) {unknown}"
