"""
Configuration.

Credentials come from environment variables only (a local .env you create
from .env.example) — nothing sensitive is ever committed.

Three connection profiles are supported out of the box:

    SNOWFLAKE_*   -> profile "snowflake"
    MSSQL_*       -> profile "mssql"
    FLATFILE_*    -> profile "flatfile"  (CSV / Excel / JSON / Parquet folder)

DEFAULT_CONNECTION picks which database the repository and CLI use by
default. A test case can name a different connection for its source and its
target, which is how cross-database (Snowflake <-> MSSQL) and file-to-table
(flat file <-> Snowflake) validation works. The flat-file profile is
read-only, so it can be a test's source or target but never the repository.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

from dotenv import load_dotenv

from src.connectors.base import ConnectionProfile

load_dotenv()  # no-op in prod where real env vars are injected

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SQL_DIR = os.path.join(ROOT_DIR, "sql")
MSSQL_SQL_DIR = os.path.join(SQL_DIR, "mssql")
DATA_DIR = os.path.join(ROOT_DIR, "data")

DEFAULT_CONNECTION = os.getenv("DEFAULT_CONNECTION", "snowflake")
DEFAULT_BATCH_ID = os.getenv("DQ_BATCH_ID", "BATCH_20260901_001")
DEFAULT_USER = os.getenv("DQ_APP_USER", os.getenv("USER", "app_user"))

# Every timestamp the application writes or shows is Indian Standard Time.
# IST is a fixed UTC+05:30 with no daylight saving, so a fixed offset is exact
# and needs no tz database (which Windows Pythons often lack).
IST = timezone(timedelta(hours=5, minutes=30), "IST")
TIMEZONE_NAME = "Asia/Kolkata"   # the same zone, as Snowflake/IANA spell it
TIMEZONE_LABEL = "IST"


def now_ist() -> datetime:
    """Current IST wall-clock time, naive — the form TIMESTAMP_NTZ columns store."""
    return datetime.now(IST).replace(tzinfo=None)


def today_ist() -> date:
    return now_ist().date()


# The demo project seeded by `python -m src.cli seed-catalog`.
DEMO_PROJECT = "Insurance Policy & Claim"


def _env(name: str, default=None):
    v = os.getenv(name)
    return v if v not in (None, "") else default


def snowflake_profile() -> ConnectionProfile:
    required = ["SNOWFLAKE_USER", "SNOWFLAKE_ACCOUNT", "SNOWFLAKE_DATABASE", "SNOWFLAKE_SCHEMA"]
    missing = [v for v in required if not _env(v)]
    if missing:
        raise EnvironmentError(
            f"Missing Snowflake environment variable(s): {', '.join(missing)}.\n"
            f"Copy .env.example to .env and fill in your own values "
            f"(see docs/SNOWFLAKE_SETUP.md for where to find each one)."
        )
    if not _env("SNOWFLAKE_PASSWORD") and not _env("SNOWFLAKE_PRIVATE_KEY_PATH") \
            and not _env("SNOWFLAKE_AUTHENTICATOR"):
        raise EnvironmentError(
            "Set SNOWFLAKE_PASSWORD, or SNOWFLAKE_PRIVATE_KEY_PATH, or "
            "SNOWFLAKE_AUTHENTICATOR=externalbrowser for an MFA-protected trial account."
        )
    return ConnectionProfile(
        name="snowflake",
        kind="snowflake",
        options={
            "user": _env("SNOWFLAKE_USER"),
            "password": _env("SNOWFLAKE_PASSWORD"),
            "account": _env("SNOWFLAKE_ACCOUNT"),
            "warehouse": _env("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
            "database": _env("SNOWFLAKE_DATABASE"),
            "schema": _env("SNOWFLAKE_SCHEMA", "PUBLIC"),
            "role": _env("SNOWFLAKE_ROLE"),
            "authenticator": _env("SNOWFLAKE_AUTHENTICATOR"),
            "private_key_path": _env("SNOWFLAKE_PRIVATE_KEY_PATH"),
            "private_key_passphrase": _env("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"),
            "timezone": TIMEZONE_NAME,
        },
    )


def mssql_profile() -> ConnectionProfile:
    required = ["MSSQL_HOST", "MSSQL_USER", "MSSQL_PASSWORD", "MSSQL_DATABASE"]
    missing = [v for v in required if not _env(v)]
    if missing:
        raise EnvironmentError(
            f"Missing MSSQL environment variable(s): {', '.join(missing)}. "
            f"MSSQL is optional — leave it unset if you only use Snowflake."
        )
    return ConnectionProfile(
        name="mssql",
        kind="mssql",
        options={
            "host": _env("MSSQL_HOST"),
            "port": int(_env("MSSQL_PORT", "1433")),
            "user": _env("MSSQL_USER"),
            "password": _env("MSSQL_PASSWORD"),
            "database": _env("MSSQL_DATABASE"),
            "odbc_driver": _env("MSSQL_ODBC_DRIVER", "ODBC Driver 18 for SQL Server"),
            "trust_server_certificate": _env("MSSQL_TRUST_CERT", "true").lower() == "true",
            "encrypt": _env("MSSQL_ENCRYPT", "true").lower() == "true",
        },
    )


def flatfile_profile() -> ConnectionProfile:
    path = os.path.abspath(os.path.expanduser(_env("FLATFILE_DIR", DATA_DIR)))
    if not os.path.exists(path):
        raise EnvironmentError(
            f"FLATFILE_DIR points at '{path}', which does not exist. "
            f"Create the folder or fix the path in .env."
        )
    delimiter = _env("FLATFILE_DELIMITER")
    return ConnectionProfile(
        name="flatfile",
        kind="flatfile",
        options={
            "path": path,
            # blank = sniff per file; "\t" in .env means a tab
            "delimiter": delimiter.replace("\\t", "\t") if delimiter else None,
            "encoding": _env("FLATFILE_ENCODING", "utf-8"),
            "decimal": _env("FLATFILE_DECIMAL", "."),   # "," for 1.234,50-style files
            "header": _env("FLATFILE_HEADER", "true").lower() == "true",
            "all_text": _env("FLATFILE_ALL_TEXT", "false").lower() == "true",
        },
    )


_BUILDERS = {"snowflake": snowflake_profile, "mssql": mssql_profile, "flatfile": flatfile_profile}

# Profiles that can hold the repository control tables. Flat files are read-only.
READ_ONLY_KINDS = {"flatfile"}


def get_profile(name: str | None = None) -> ConnectionProfile:
    name = (name or DEFAULT_CONNECTION).strip().lower()
    try:
        return _BUILDERS[name]()
    except KeyError:
        raise ValueError(
            f"Unknown connection '{name}'. Available: {', '.join(sorted(_BUILDERS))}"
        ) from None


def available_connections() -> list[str]:
    """Profiles whose environment variables are actually filled in."""
    out = []
    for name, build in _BUILDERS.items():
        try:
            build()
            out.append(name)
        except EnvironmentError:
            continue
    return out


def repository_connections() -> list[str]:
    """Configured profiles that can host the repository (i.e. not read-only)."""
    return [n for n in available_connections() if n not in READ_ONLY_KINDS]


def new_batch_id(prefix: str = "BATCH") -> str:
    return f"{prefix}_{now_ist().strftime('%Y%m%d_%H%M%S')}"
