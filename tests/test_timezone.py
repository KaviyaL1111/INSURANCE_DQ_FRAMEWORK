"""Every timestamp the application writes is Indian Standard Time."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


from src import config
from src.repository import TestCase


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _assert_ist(ts, tolerance=timedelta(minutes=1)):
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    assert ts.tzinfo is None, "stored timestamps are naive IST wall-clock values"
    assert abs(ts - (_utc_now() + timedelta(hours=5, minutes=30))) < tolerance, ts


def test_now_ist_is_utc_plus_five_thirty():
    _assert_ist(config.now_ist())
    assert config.today_ist() == config.now_ist().date()


def test_batch_and_run_ids_use_ist(monkeypatch):
    fixed = datetime(2026, 9, 23, 23, 45, 0)
    monkeypatch.setattr(config, "now_ist", lambda: fixed)
    assert config.new_batch_id() == "BATCH_20260923_234500"
    import src.history as history
    monkeypatch.setattr(history, "now_ist", lambda: fixed)
    assert history.new_run_id().startswith("RUN_20260923_234500_")


def test_repository_stamps_are_ist(repo, project):
    _assert_ist(repo.list_projects()[0]["CREATED_TS"])
    folder = repo.create_folder(project["PROJECT_ID"], "TZ")
    tc = repo.create_test_case(TestCase(
        project_id=project["PROJECT_ID"], folder_id=folder["FOLDER_ID"], test_name="tz",
        validation_sql="SELECT 1 WHERE 1 = 0"))
    _assert_ist(tc.created_ts)
    _assert_ist(tc.updated_ts)


def test_execution_history_and_run_params_are_ist(engine, repo, project):
    folder = repo.create_folder(project["PROJECT_ID"], "TZ")
    tc = repo.create_test_case(TestCase(
        project_id=project["PROJECT_ID"], folder_id=folder["FOLDER_ID"], test_name="tz run",
        source_connection="sqlite", validation_sql="SELECT :run_ts AS X WHERE 1 = 0"))
    engine.run_test_case(tc.test_case_id)
    row = engine.history.get_execution_history(
        "1970-01-01", "2999-12-31", project_id=project["PROJECT_ID"])[0]
    _assert_ist(row["RUN_TS"])
    _assert_ist(engine.resolve_params(tc, None, "B")["run_ts"])


def test_snowflake_session_runs_in_ist(monkeypatch):
    """CURRENT_TIMESTAMP() and DEFAULT CURRENT_TIMESTAMP() columns follow the session zone."""
    for var, val in {"SNOWFLAKE_USER": "u", "SNOWFLAKE_ACCOUNT": "a", "SNOWFLAKE_DATABASE": "d",
                     "SNOWFLAKE_SCHEMA": "s", "SNOWFLAKE_PASSWORD": "p"}.items():
        monkeypatch.setenv(var, val)
    captured = {}
    import sys
    import types
    fake = types.ModuleType("snowflake.connector")
    fake.connect = lambda **kw: captured.update(kw) or object()
    package = types.ModuleType("snowflake")
    package.connector = fake
    monkeypatch.setitem(sys.modules, "snowflake", package)
    monkeypatch.setitem(sys.modules, "snowflake.connector", fake)
    from src.connectors import get_connector
    get_connector("snowflake")._connect()
    assert captured["session_parameters"] == {"TIMEZONE": "Asia/Kolkata"}
