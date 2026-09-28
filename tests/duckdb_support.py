"""
Run the Snowflake pipeline scripts on DuckDB.

The ETL scripts (03 transform, 04 curated MERGE) use Snowflake SQL that
SQLite can't run — MERGE above all. DuckDB can, once a handful of
Snowflake-isms are rewritten, so the transformation rules and the seeded
curated validations get exercised for real without credentials. Tests that
need it skip when the ``duckdb`` package isn't installed.
"""
from __future__ import annotations

import re

from src.connectors.base import (PARAM_RE, ConnectionProfile, Connector, QueryResult,
                                 _strip_literals)

_MACROS = [
    "CREATE MACRO TO_VARCHAR(x) AS CAST(x AS VARCHAR)",
    "CREATE MACRO INITCAP(s) AS array_to_string(list_transform(string_split(lower(s), ' '), "
    "w -> upper(w[1]) || w[2:]), ' ')",
]


def to_duckdb(sql: str) -> str:
    sql = sql.replace("CURRENT_TIMESTAMP()", "CAST(CURRENT_TIMESTAMP AS TIMESTAMP)")
    sql = re.sub(r"\bTO_CHAR\(([^,]+),\s*'YYYY-MM'\)", r"strftime(CAST(\1 AS DATE), '%Y-%m')", sql)
    sql = re.sub(r"\bTIMESTAMP_NTZ\b", "TIMESTAMP", sql)
    sql = re.sub(r"\bNUMBER\(", "DECIMAL(", sql)
    sql = re.sub(r"\bDATEDIFF\(\s*day\s*,", "DATEDIFF('day',", sql, flags=re.I)
    sql = re.sub(r"\bDATEADD\(\s*day\s*,\s*(\d+)\s*,\s*([^)]+)\)",
                 r"(CAST(\2 AS DATE) + INTERVAL \1 DAY)", sql, flags=re.I)
    # MERGE ... UPDATE SET T.col = ...  ->  col = ...  (a SET target always starts its line)
    sql = re.sub(r"^(\s*)T\.(\w+)(\s*)=", r"\1\2\3=", sql, flags=re.M)
    return sql


class DuckDBConnector(Connector):
    kind = "duckdb"

    def __init__(self):
        super().__init__(ConnectionProfile(name="duckdb", kind="duckdb", options={}))

    def _connect(self):
        import duckdb
        conn = duckdb.connect(":memory:")
        for m in _MACROS:
            conn.execute(m)
        conn.begin()  # behave like a DB-API driver: changes wait for commit()
        return conn

    def bind(self, sql: str, params: dict):
        self._check_params(sql, params)
        masked = _strip_literals(sql)
        out, last, used = [], 0, {}
        for m in PARAM_RE.finditer(masked):
            out.append(sql[last:m.start()] + f"${m.group(1)}")
            used[m.group(1)] = params[m.group(1)]
            last = m.end()
        out.append(sql[last:])
        return to_duckdb("".join(out)), (used or None)

    # DuckDB cursors are separate connections; use the one connection throughout.
    def query(self, sql, params=None):
        bound, args = self.bind(sql, params or {})
        cur = self.connection.execute(bound, args) if args else self.connection.execute(bound)
        if cur.description is None:
            return QueryResult([], [])
        cols = [c[0] for c in cur.description]
        return QueryResult(cols, [tuple(r) for r in cur.fetchall()])

    def execute(self, sql, params=None):
        bound, args = self.bind(sql, params or {})
        cur = self.connection.execute(bound, args) if args else self.connection.execute(bound)
        try:
            rows = cur.fetchall()
            return int(rows[0][0]) if rows and len(rows[0]) == 1 else -1
        except Exception:
            return -1

    def commit(self):
        self.connection.commit()
        self.connection.begin()

    def rollback(self):
        self.connection.rollback()
        self.connection.begin()
