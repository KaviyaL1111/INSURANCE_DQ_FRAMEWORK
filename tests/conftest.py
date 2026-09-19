"""
Test fixtures.

Most tests run against an in-memory SQLite database through a Connector
subclass, so the repository, engine and regression flow are exercised for
real without needing Snowflake credentials. A small dialect shim rewrites
the handful of Snowflake-isms in the repository DDL; SQLite speaks the same
``:named`` parameter style the framework uses, so no binding translation is
needed.

The Snowflake integration tests live in test_integration_snowflake.py and
skip automatically when no credentials are configured.
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
from datetime import date, datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.connectors.base import ConnectionProfile, Connector  # noqa: E402

# Microsecond precision matters: "the latest run of each test case" is ordered
# by RUN_TS, and truncating to whole seconds makes several runs in the same
# second indistinguishable, so the tiebreak falls to a random uuid.
sqlite3.register_adapter(datetime, lambda v: v.isoformat(sep=" ", timespec="microseconds"))
sqlite3.register_adapter(date, lambda v: v.isoformat())


def to_sqlite(sql: str) -> str:
    """Rewrite the few Snowflake-specific constructs the repository uses."""
    sql = sql.replace("CURRENT_TIMESTAMP()", "CURRENT_TIMESTAMP")
    sql = re.sub(r"CREATE\s+OR\s+REPLACE\s+VIEW", "CREATE VIEW IF NOT EXISTS", sql, flags=re.I)
    return sql


class SQLiteConnector(Connector):
    """A Connector over sqlite3 — used only by the test suite."""

    kind = "sqlite"

    def __init__(self, path: str = ":memory:"):
        super().__init__(ConnectionProfile(name="sqlite", kind="sqlite", options={"path": path}))
        self._path = path

    def _connect(self):
        conn = sqlite3.connect(self._path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def bind(self, sql: str, params: dict):
        self._check_params(sql, params)
        return to_sqlite(sql), (params or None)


@pytest.fixture
def db():
    conn = SQLiteConnector()
    yield conn
    conn.close()


@pytest.fixture
def repo(db, monkeypatch):
    from src.repository import Repository
    monkeypatch.setattr("src.config.DEFAULT_USER", "pytest")
    r = Repository(connector=db)
    r.init_schema()
    yield r


@pytest.fixture
def project(repo):
    return repo.create_project("Test Project", "created by the test suite")


@pytest.fixture
def demo_tables(db):
    """A miniature source -> target pair, mirroring the brief's Notes section."""
    db.execute("""CREATE TABLE SRC_POLICY (
                      POLICY_ID TEXT PRIMARY KEY, CUSTOMER_ID TEXT, POLICY_TYPE TEXT,
                      PREMIUM_AMOUNT REAL, STATUS TEXT)""")
    db.execute("""CREATE TABLE TGT_POLICY (
                      POLICY_ID TEXT PRIMARY KEY, CUSTOMER_ID TEXT, POLICY_TYPE TEXT,
                      PREMIUM_AMOUNT REAL, STATUS TEXT)""")
    rows = [
        ("P1001", "C001", "Automobile", 19101.97, "Active"),
        ("P1002", "C002", "Life Insurance", 8831.25, "Inactive"),
        ("P1003", "C003", "Home Insurance", 4210.00, "Active"),
        ("P1004", "C004", "Health Insurance", 15300.50, "Active"),
        ("P1005", "C005", "Automobile", 12000.00, "Active"),
    ]
    for r in rows:
        db.execute(
            "INSERT INTO SRC_POLICY VALUES (:a, :b, :c, :d, :e)",
            dict(zip("abcde", r)),
        )
        db.execute(
            "INSERT INTO TGT_POLICY VALUES (:a, :b, :c, :d, :e)",
            dict(zip("abcde", r)),
        )
    db.commit()
    return rows


@pytest.fixture
def engine(repo):
    from src.dq_engine import DQEngine
    eng = DQEngine(repository=repo, executed_by="pytest")
    # every test case in these tests targets the same in-memory database
    eng._connectors["snowflake"] = repo.db
    eng._connectors["sqlite"] = repo.db
    yield eng
    eng.close()
