"""
MSSQL connector.

Prefers pymssql (pyformat binding, pip-installable with no system driver);
falls back to pyodbc (qmark binding) when pymssql isn't present. Both paths
accept the same ``:named`` SQL as the Snowflake connector, so a saved test
case moves between the two databases unchanged.
"""
from __future__ import annotations

from src.connectors.base import Connector, PARAM_RE, _strip_literals
from src.connectors.snowflake_connector import _sub_outside_literals


class MSSQLConnector(Connector):
    kind = "mssql"

    def __init__(self, profile):
        super().__init__(profile)
        self._driver = None  # 'pymssql' | 'pyodbc'

    def _connect(self):
        o = self.profile.options
        try:
            import pymssql
            self._driver = "pymssql"
            return pymssql.connect(
                server=o["host"],
                port=int(o.get("port", 1433)),
                user=o["user"],
                password=o["password"],
                database=o["database"],
                autocommit=False,
                login_timeout=30,
            )
        except ImportError:
            pass

        import pyodbc
        self._driver = "pyodbc"
        driver = o.get("odbc_driver", "ODBC Driver 18 for SQL Server")
        conn_str = (
            f"DRIVER={{{driver}}};SERVER={o['host']},{o.get('port', 1433)};"
            f"DATABASE={o['database']};UID={o['user']};PWD={o['password']};"
            f"TrustServerCertificate={'yes' if o.get('trust_server_certificate', True) else 'no'};"
            f"Encrypt={'yes' if o.get('encrypt', True) else 'no'}"
        )
        return pyodbc.connect(conn_str, timeout=30)

    def bind(self, sql: str, params: dict):
        self._check_params(sql, params)
        self.connection  # force driver selection

        if self._driver == "pymssql":
            used = set()

            def repl(m):
                used.add(m.group(1))
                return f"%({m.group(1)})s"

            out = _sub_outside_literals(sql, repl)
            return out, {k: v for k, v in params.items() if k in used} or None

        # pyodbc: positional qmark, arguments in order of appearance.
        ordered = []
        masked = _strip_literals(sql)
        pieces, last = [], 0
        for m in PARAM_RE.finditer(masked):
            pieces.append(sql[last:m.start()])
            pieces.append("?")
            ordered.append(params[m.group(1)])
            last = m.end()
        pieces.append(sql[last:])
        return "".join(pieces), (tuple(ordered) if ordered else None)
