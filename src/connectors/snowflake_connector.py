"""Snowflake connector (snowflake-connector-python, pyformat binding)."""
from __future__ import annotations

from src.connectors.base import Connector, ConnectionProfile, PARAM_RE, _strip_literals


class SnowflakeConnector(Connector):
    kind = "snowflake"

    def _connect(self):
        import snowflake.connector  # imported lazily so MSSQL-only users need not install it

        o = self.profile.options
        kwargs = dict(
            user=o["user"],
            account=o["account"],
            warehouse=o.get("warehouse"),
            database=o.get("database"),
            schema=o.get("schema"),
            role=o.get("role"),
            client_session_keep_alive=True,
            application="InsuranceDQFramework",
            # CURRENT_TIMESTAMP() — and every DEFAULT CURRENT_TIMESTAMP() column —
            # follows the session TIMEZONE, which Snowflake defaults to Los Angeles.
            session_parameters={"TIMEZONE": o.get("timezone", "Asia/Kolkata")},
        )
        # Password OR key-pair OR external browser (Snowflake trials with MFA).
        if o.get("authenticator"):
            kwargs["authenticator"] = o["authenticator"]
        if o.get("password"):
            kwargs["password"] = o["password"]
        if o.get("private_key_path"):
            kwargs["private_key"] = _load_private_key(o["private_key_path"], o.get("private_key_passphrase"))
        return snowflake.connector.connect(**{k: v for k, v in kwargs.items() if v is not None})

    def bind(self, sql: str, params: dict):
        self._check_params(sql, params)
        used = set()

        def repl(m):
            used.add(m.group(1))
            return f"%({m.group(1)})s"

        # Rebuild with literals preserved: substitute only outside literals/comments.
        out = _sub_outside_literals(sql, repl)
        # Escape stray % so Snowflake's pyformat binder doesn't choke (e.g. LIKE '%x%').
        return out, {k: v for k, v in params.items() if k in used} or None


def _sub_outside_literals(sql: str, repl):
    """Apply PARAM_RE substitution only in the non-literal, non-comment regions."""
    masked = _strip_literals(sql)
    result, last = [], 0
    for m in PARAM_RE.finditer(masked):
        result.append(sql[last:m.start()])
        result.append(repl(m))
        last = m.end()
    result.append(sql[last:])
    return "".join(result)


def _load_private_key(path: str, passphrase: str | None):
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization

    with open(path, "rb") as f:
        p_key = serialization.load_pem_private_key(
            f.read(),
            password=passphrase.encode() if passphrase else None,
            backend=default_backend(),
        )
    return p_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
