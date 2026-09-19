"""
Execution History & Rerun.

The exact flow from the brief: open history, enter a start and end date, load
matching executions, tick the ones you want, press Execute Selected Tests.
Each selected test is reloaded from the repository at its latest saved
configuration before it runs.
"""
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from common import (batch_id, get_regression_engine, get_repository, page_setup,
                    param_inputs, show_results, sidebar, STATUS_ICON)

page_setup("Execution History", "🕘")
project = sidebar()
repo = get_repository()
pid = project["PROJECT_ID"]

st.title("Execution History")
st.caption("Filter past executions by date, select the ones you need, and rerun them. "
           "A rerun always uses each test case's latest saved configuration.")

# ---------------------------------------------------------------- 2. filter
with st.form("history_filter"):
    c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
    start = c1.date_input("Start date", value=date.today() - timedelta(days=7))
    end = c2.date_input("End date", value=date.today())
    status = c3.selectbox("Status", ["(any)", "FAIL", "ERROR", "PASS"])
    latest = c4.checkbox("Latest run per test only", value=False,
                         help="Collapse repeated runs of the same test case.")
    loaded = st.form_submit_button("🔍 Load history", type="primary")

if loaded:
    if start > end:
        st.error("The start date is after the end date.")
        st.stop()
    rows = get_regression_engine().get_execution_history(
        start, end, project_id=pid,
        status="" if status == "(any)" else status, latest_per_test=latest)
    st.session_state["history_rows"] = rows
    st.session_state["history_window"] = (start.isoformat(), end.isoformat())

rows = st.session_state.get("history_rows")
if rows is None:
    st.info("Choose a date range and press **Load history**.")
    st.stop()
if not rows:
    st.warning(f"No executions found between {start} and {end}. "
               "Run some test cases first, or widen the range.")
    st.stop()

# ------------------------------------------------- 3./4. display with checkboxes
window = st.session_state.get("history_window", ("", ""))
st.success(f"{len(rows)} execution(s) between {window[0]} and {window[1]}")

frame = pd.DataFrame([{
    "Select": False,
    "": STATUS_ICON.get(r["STATUS"], "⚪"),
    "Test case": r["TEST_CASE_ID"],
    "Name": r["TEST_NAME"] or "",
    "Folder": r["FOLDER_PATH"] or "",
    "Run at": r["RUN_TS"],
    "Status": r["STATUS"],
    "Failed records": r["FAILED_RECORD_COUNT"],
    "Mode": r["RUN_MODE"],
    "Ran version": r["TEST_VERSION_NO"],
    "By": r["EXECUTED_BY"],
    "_execution_id": r["EXECUTION_ID"],
} for r in rows])

c1, c2, c3 = st.columns([1, 1, 4])
if c1.button("Select all failing", use_container_width=True):
    st.session_state["preselect"] = {r["TEST_CASE_ID"] for r in rows
                                     if r["STATUS"] in ("FAIL", "ERROR")}
    st.rerun()
if c2.button("Clear selection", use_container_width=True):
    st.session_state["preselect"] = set()
    st.rerun()

preselect = st.session_state.get("preselect", set())
if preselect:
    frame["Select"] = frame["Test case"].isin(preselect)

edited = st.data_editor(
    frame.drop(columns=["_execution_id"]),
    use_container_width=True, hide_index=True,
    disabled=[c for c in frame.columns if c not in ("Select", "_execution_id")],
    column_config={
        "Select": st.column_config.CheckboxColumn("Select", help="Tick to include in the rerun"),
        "": st.column_config.TextColumn("", width="small"),
    },
    key="history_editor",
)

selected_ids = list(dict.fromkeys(edited.loc[edited["Select"], "Test case"].tolist()))

# ------------------------------------------- 5./6./7. select and execute
st.divider()
if not selected_ids:
    st.info("Tick one or more rows above to enable the rerun.")
else:
    st.markdown(f"**{len(selected_ids)} test case(s) selected:** `{', '.join(selected_ids)}`")
    cases = repo.get_test_cases(selected_ids)
    needed, defaults = [], {}
    for tc in cases:
        defaults.update(tc.param_defaults or {})
        for p in tc.required_params():
            if p not in needed:
                needed.append(p)
    overrides = param_inputs(needed, defaults, "history")

    versions = {tc.test_case_id: tc.version_no for tc in cases}
    ran_versions = {r["TEST_CASE_ID"]: r["TEST_VERSION_NO"] for r in rows}
    changed = [t for t in selected_ids
               if ran_versions.get(t) and versions[t] != ran_versions[t]]
    if changed:
        st.info(f"{len(changed)} selected test case(s) have been edited since they last ran. "
                f"The rerun will use the current saved version: "
                + ", ".join(f"{t} v{ran_versions[t]}→v{versions[t]}" for t in changed))

    if st.button("🔁 Execute Selected Tests", type="primary"):
        eng = get_regression_engine()
        progress = st.progress(0.0, text="Starting…")

        def tick(done, total, result):
            progress.progress(done / total,
                              text=f"{done}/{total} · {result.test_case_id} {result.status}")

        outcome = eng.rerun_selected(
            selected_ids, params=overrides, batch_id=batch_id(), project_id=pid,
            triggered_from="EXECUTION_HISTORY",
            history_start=window[0] or None, history_end=window[1] or None,
            on_progress=tick)
        progress.empty()

        st.subheader("Rerun result")
        if outcome.all_passed:
            st.success(f"All {outcome.total} rerun test case(s) passed. {outcome.summary()}")
        else:
            st.error(outcome.summary())
        for tcid, why in outcome.skipped:
            st.warning(f"Skipped {tcid}: {why}")
        show_results(outcome.results, eng.history)
        st.session_state.pop("history_rows", None)
        st.caption("History reloaded on your next search — this rerun is now part of it.")

# -------------------------------------------------------------- audit trail
with st.expander("Regression run audit trail"):
    runs = get_regression_engine().history.regression_runs(project_id=pid, limit=25)
    if runs:
        st.dataframe(pd.DataFrame(runs), use_container_width=True, hide_index=True)
    else:
        st.caption("No regression runs recorded yet.")
