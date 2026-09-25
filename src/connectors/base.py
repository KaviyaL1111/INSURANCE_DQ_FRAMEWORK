"""
Database connector abstraction.

Every saved test case stores plain SQL with ``:named`` placeholders, e.g.::

    SELECT * FROM STG_POLICY WHERE BATCH_ID = :batch_id

Each connector translates that neutral syntax into whatever its own driver
speaks (pyformat for Snowflake/pymssql, qmark for pyodbc), so the *same*
saved test case can be pointed at Snowflake or MSSQL without editing it.
That portability is the whole reason this layer exists.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Sequence

# :name  — but NOT ::CAST (Snowflake) and not inside an identifier.
PARAM_RE = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def to_dicts(self) -> list[dict]:
        return [dict(zip(self.columns, r)) for r in self.rows]

    def to_frame(self):
        import pandas as pd
        return pd.DataFrame(self.rows, columns=self.columns)


@dataclass
class ConnectionProfile:
    """One named database this app can talk to (from .env)."""
    name: str
    kind: str                       # 'snowflake' | 'mssql' | 'flatfile'
    options: dict = field(default_factory=dict)


def extract_params(sql: str) -> list[str]:
    """Ordered, de-duplicated :param names used by a piece of SQL."""
    seen, out = set(), []
    for m in PARAM_RE.finditer(_strip_literals(sql)):
        if m.group(1) not in seen:
            seen.add(m.group(1))
            out.append(m.group(1))
    return out


def _strip_literals(sql: str) -> str:
    """
    Blank out '...' literals and -- / /* */ comments so we never bind inside them.

    The result is the SAME LENGTH as the input — each masked character becomes a
    space. Callers locate parameters in this masked copy and then slice the
    ORIGINAL string at those offsets, so any length change here silently
    corrupts the SQL it produces.
    """
    def blank(m: "re.Match") -> str:
        return " " * (m.end() - m.start())

    sql = re.sub(r"--[^\n]*", blank, sql)
    sql = re.sub(r"/\*.*?\*/", blank, sql, flags=re.S)
    return re.sub(r"'(?:[^']|'')*'", blank, sql)


class Connector(ABC):
    """Minimal read/write surface the engine needs from any database."""

    kind: str = "generic"

    def __init__(self, profile: ConnectionProfile):
        self.profile = profile
        self._conn = None

    # ---- lifecycle -----------------------------------------------------
    @abstractmethod
    def _connect(self):
        """Return a live DB-API connection."""

    @property
    def connection(self):
        if self._conn is None:
            self._conn = self._connect()
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # ---- binding -------------------------------------------------------
    @abstractmethod
    def bind(self, sql: str, params: dict) -> tuple[str, Any]:
        """Translate :named placeholders into this driver's parameter style."""

    def _check_params(self, sql: str, params: dict) -> None:
        needed = extract_params(sql)
        missing = [p for p in needed if p not in params]
        if missing:
            raise KeyError(
                f"Missing value(s) for parameter(s): {', '.join(missing)}. "
                f"This SQL expects: {', '.join(needed) or '(none)'}"
            )

    # ---- execution -----------------------------------------------------
    def query(self, sql: str, params: dict | None = None) -> QueryResult:
        bound_sql, args = self.bind(sql, params or {})
        cur = self.connection.cursor()
        try:
            cur.execute(bound_sql, args) if args is not None else cur.execute(bound_sql)
            columns = [c[0] for c in cur.description] if cur.description else []
            rows = cur.fetchall() if cur.description else []
            return QueryResult(columns=columns, rows=[tuple(r) for r in rows])
        finally:
            cur.close()

    def execute(self, sql: str, params: dict | None = None) -> int:
        """Run a non-SELECT. Returns affected row count where the driver reports one."""
        bound_sql, args = self.bind(sql, params or {})
        cur = self.connection.cursor()
        try:
            cur.execute(bound_sql, args) if args is not None else cur.execute(bound_sql)
            return cur.rowcount if cur.rowcount is not None else -1
        finally:
            cur.close()

    def execute_many(self, sql: str, seq_of_params: Sequence[dict]) -> int:
        n = 0
        for p in seq_of_params:
            self.execute(sql, p)
            n += 1
        return n

    def commit(self) -> None:
        self.connection.commit()

    def rollback(self) -> None:
        try:
            self.connection.rollback()
        except Exception:
            pass

    # ---- scripts -------------------------------------------------------
    def run_script(self, sql_text: str, params: dict | None = None) -> list[QueryResult | None]:
        """Execute a multi-statement script, statement by statement."""
        results = []
        for stmt in split_statements(sql_text):
            try:
                results.append(self.query(stmt, params))
            except Exception as exc:
                if _is_no_result_set(exc):
                    results.append(None)
                else:
                    raise
        self.commit()
        return results


def _is_no_result_set(exc: Exception) -> bool:
    return "no results" in str(exc).lower()


def split_statements(sql_text: str) -> list[str]:
    """
    Split a script on semicolons that are outside string literals, line
    comments and block comments. The naive ``text.split(';')`` this replaces
    corrupted any statement containing a ';' inside quotes.
    """
    statements, buf = [], []
    i, n = 0, len(sql_text)
    in_single = in_line_comment = in_block_comment = False
    while i < n:
        ch = sql_text[i]
        nxt = sql_text[i + 1] if i + 1 < n else ""
        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
                buf.append(ch)
        elif in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 1
        elif in_single:
            buf.append(ch)
            if ch == "'":
                if nxt == "'":       # escaped quote ''
                    buf.append(nxt)
                    i += 1
                else:
                    in_single = False
        elif ch == "-" and nxt == "-":
            in_line_comment = True
            i += 1
        elif ch == "/" and nxt == "*":
            in_block_comment = True
            i += 1
        elif ch == "'":
            in_single = True
            buf.append(ch)
        elif ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements
