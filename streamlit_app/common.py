"""Shared helpers for the Streamlit pages."""
from __future__ import annotations

import os
import sys

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import DEFAULT_BATCH_ID, DEFAULT_CONNECTION, available_connections  # noqa: E402
from src.dq_engine import DQEngine  # noqa: E402
from src.history import HistoryStore  # noqa: E402
from src.regression_engine import RegressionEngine  # noqa: E402
from src.repository import Repository  # noqa: E402

STATUS_ICON = {"PASS": "🟢", "FAIL": "🔴", "ERROR": "🟠"}


def page_setup(title: str, icon: str = "🛡️") -> None:
    st.set_page_config(page_title=f"{title} · DQ Automation", page_icon=icon, layout="wide")


@st.cache_resource(show_spinner="Connecting…")
def _cached_repository(connection: str, nonce: int) -> Repository:
    """One connection per Streamlit session. `nonce` lets Reconnect bust the cache."""
    return Repository(connection_name=connection)


def get_repository() -> Repository:
    connection = st.session_state.get("connection", DEFAULT_CONNECTION)
    nonce = st.session_state.get("conn_nonce", 0)
    return _cached_repository(connection, nonce)


def reconnect() -> None:
    st.session_state["conn_nonce"] = st.session_state.get("conn_nonce", 0) + 1
    _cached_repository.clear()


def get_history() -> HistoryStore:
    return HistoryStore(get_repository().db)


def get_engine() -> DQEngine:
    return DQEngine(repository=get_repository())


def get_regression_engine() -> RegressionEngine:
    return RegressionEngine(repository=get_repository())


def sidebar(require_project: bool = True):
    """Connection picker, project picker and batch id. Returns the project dict."""
    st.sidebar.title("🛡️ DQ Automation")

    profiles = available_connections()
    if not profiles:
        st.sidebar.error("No connection is configured. Copy `.env.example` to `.env` "
                         "and fill in your Snowflake details.")
        st.stop()
    st.session_state.setdefault("connection", DEFAULT_CONNECTION
                                if DEFAULT_CONNECTION in profiles else profiles[0])
    st.sidebar.selectbox("Connection", profiles, key="connection")

    try:
        repo = get_repository()
        projects = repo.list_projects()
    except Exception as exc:
        st.sidebar.error("Could not reach the database.")
        st.error(f"**Connection failed**\n\n```\n{exc}\n```\n\n"
                 "Check your `.env`, then run `python -m src.cli doctor` in a terminal.")
        if st.button("Retry connection"):
            reconnect()
            st.rerun()
        st.stop()

    if not projects:
        if require_project:
            st.sidebar.warning("No projects yet.")
            st.info("**No project exists yet.** Create one on the *Projects & Folders* page, "
                    "or run `python -m src.cli seed-catalog` to load the demo project.")
            st.stop()
        return None

    names = [p["PROJECT_NAME"] for p in projects]
    st.session_state.setdefault("project_name", names[0])
    if st.session_state["project_name"] not in names:
        st.session_state["project_name"] = names[0]
    chosen = st.sidebar.selectbox("Application / Project", names, key="project_name")

    st.sidebar.text_input("Batch ID", value=DEFAULT_BATCH_ID, key="batch_id",
                          help="Passed to every test case as :batch_id")
    st.sidebar.divider()
    if st.sidebar.button("↻ Reconnect", use_container_width=True):
        reconnect()
        st.rerun()

    return next(p for p in projects if p["PROJECT_NAME"] == chosen)


def batch_id() -> str:
    return st.session_state.get("batch_id", DEFAULT_BATCH_ID)


def folder_options(repo: Repository, project_id: str) -> dict[str, str]:
    """{display label: folder_id}, indented to show the tree."""
    out = {}
    for f in repo.folder_tree(project_id):
        depth = f["FOLDER_PATH"].count("/") - 1
        tag = " 🔁" if f["FOLDER_TYPE"] == "REGRESSION" else ""
        out[f"{'　' * depth}{f['FOLDER_NAME']}{tag}  ({f['TEST_COUNT']})"] = f["FOLDER_ID"]
    return out


def results_frame(results) -> pd.DataFrame:
    return pd.DataFrame([{
        "": STATUS_ICON.get(r.status, "⚪"),
        "Test": r.test_case_id,
        "Name": r.test_name,
        "Status": r.status,
        "Failed records": r.failed_record_count,
        "Source rows": r.source_row_count,
        "Target rows": r.target_row_count,
        "Matched": r.matched_record_count,
        "Time": f"{r.duration_ms} ms",
        "Error": (r.error_message or "")[:200],
    } for r in results])


def show_results(results, history: HistoryStore | None = None) -> None:
    """Result table, headline metrics, and the mismatch detail behind each failure."""
    if not results:
        st.info("No test cases were executed.")
        return
    passed = sum(1 for r in results if r.status == "PASS")
    failed = sum(1 for r in results if r.status == "FAIL")
    errored = sum(1 for r in results if r.status == "ERROR")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Executed", len(results))
    c2.metric("Passed", passed)
    c3.metric("Failed", failed, delta=None if not failed else f"-{failed}", delta_color="inverse")
    c4.metric("Pass rate", f"{passed / len(results) * 100:.0f}%")
    if errored:
        st.warning(f"{errored} test case(s) could not run — see the Error column.")

    df = results_frame(results)
    st.dataframe(df.drop(columns=["Error"] if not errored else []),
                 use_container_width=True, hide_index=True)

    failing = [r for r in results if r.status == "FAIL" and r.failures]
    if failing:
        st.subheader("Failed records — mismatch detail")
        for r in failing:
            with st.expander(f"🔴 {r.test_case_id} · {r.test_name} — "
                             f"{r.failed_record_count} failed record(s)", expanded=len(failing) == 1):
                st.dataframe(failures_frame(r.failures), use_container_width=True,
                             hide_index=True)


def failures_frame(failures) -> pd.DataFrame:
    return pd.DataFrame([{
        "Business key": f.business_key,
        "Column": f.column_name,
        "Expected": f.expected_value,
        "Actual": f.actual_value,
        "Type": f.failure_type,
        "Reason": f.failure_reason,
    } for f in failures])


def param_inputs(required: list[str], defaults: dict, key_prefix: str) -> dict:
    """Render an input for every :placeholder a test case needs."""
    if not required:
        return {}
    st.caption("Parameters required by this test case")
    values = {}
    cols = st.columns(min(len(required), 3))
    for i, name in enumerate(required):
        if name in ("batch_id", "run_date", "run_ts"):
            continue
        values[name] = cols[i % len(cols)].text_input(
            f":{name}", value=str(defaults.get(name, "")), key=f"{key_prefix}_param_{name}")
    return {k: v for k, v in values.items() if v != ""}
