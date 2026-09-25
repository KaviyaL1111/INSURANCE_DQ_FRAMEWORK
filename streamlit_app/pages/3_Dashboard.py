"""Current data-quality state: pass/fail from each test case's latest run."""
import pandas as pd
import streamlit as st

from common import (batch_id, fmt_ts, get_history, get_regression_engine, get_repository,
                    page_header, page_setup, show_results, sidebar, status_label)

page_setup("Dashboard", "📊")
project = sidebar()
repo = get_repository()
history = get_history()
pid = project["PROJECT_ID"]

page_header("📊 Dashboard",
            "Every figure below reflects the **most recent run of each test case**, so a "
            "corrected defect stops being counted the moment it is revalidated.")

summary = history.dashboard_summary(pid)
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Test cases run", summary["total"])
c2.metric("Passed", summary["passed"])
c3.metric("Failed", summary["failed"],
          delta=None if not summary["failed"] else f"{summary['failed']} failing",
          delta_color="inverse")
c4.metric("Pass rate", f"{summary['pass_pct']}%" if summary["pass_pct"] is not None else "—")
c5.metric("Failed records", summary["failed_records"])

if summary["total"] == 0:
    st.info("Nothing has been executed yet. Run some test cases from the **Test Cases** page.")
    st.stop()

st.progress((summary["passed"] / summary["total"]) if summary["total"] else 0.0,
            text=f"{summary['passed']} passed · {summary['failed']} failed · "
                 f"{summary['errored']} could not run")
st.divider()

# ------------------------------------------------------------ latest status
latest = history.get_execution_history("1970-01-01", "2999-12-31",
                                       project_id=pid, latest_per_test=True)
st.subheader("Latest status by test case")
if latest:
    status_frame = pd.DataFrame([{
        "Status": status_label(r["STATUS"]),
        "Test case": r["TEST_CASE_ID"],
        "Name": r["TEST_NAME"],
        "Folder": r["FOLDER_PATH"],
        "Failed records": r["FAILED_RECORD_COUNT"],
        "Last run (IST)": fmt_ts(r["RUN_TS"]),
        "Version": r["TEST_VERSION_NO"],
        "Error": (r.get("ERROR_MESSAGE") or "")[:300],
    } for r in latest])
    show = st.segmented_control(
        "Show", ["All", "Failing", "Could not run", "Passing"], default="All",
        key="dash_status_filter", label_visibility="collapsed") or "All"
    wanted = {"Failing": "FAIL", "Could not run": "ERROR", "Passing": "PASS"}.get(show)
    view = status_frame if wanted is None else \
        status_frame[[r["STATUS"] == wanted for r in latest]]
    if not summary["errored"]:
        view = view.drop(columns=["Error"])
    st.dataframe(view, use_container_width=True, hide_index=True,
                 column_config={"Error": st.column_config.TextColumn(
                     "Why it could not run", width="large")})
    if summary["errored"] and show == "All":
        st.caption(f"🟠 {summary['errored']} test case(s) could not run — the reason is in the "
                   "last column. Pick *Could not run* above to see only those.")

# -------------------------------------------------------- failed record detail
st.subheader("Failed records — expected vs actual")
failures = history.latest_failures_for_project(pid, limit=500)
if not failures:
    st.success("No failing records in the latest run of any test case. 🎉")
else:
    detail = pd.DataFrame([{
        "Test case": f["TEST_CASE_ID"],
        "Business key": f["BUSINESS_KEY"],
        "Column": f["COLUMN_NAME"],
        "Expected": f["EXPECTED_VALUE"],
        "Actual": f["ACTUAL_VALUE"],
        "Type": f["FAILURE_TYPE"],
        "Reason": f["FAILURE_REASON"],
        "When (IST)": fmt_ts(f["FAILED_TS"]),
    } for f in failures])

    c1, c2 = st.columns([1, 1])
    kinds = c1.multiselect("Failure type", sorted(detail["Type"].unique()))
    tests = c2.multiselect("Test case", sorted(detail["Test case"].unique()))
    view = detail
    if kinds:
        view = view[view["Type"].isin(kinds)]
    if tests:
        view = view[view["Test case"].isin(tests)]

    st.dataframe(view, use_container_width=True, hide_index=True)
    st.download_button("⬇ Download as CSV", view.to_csv(index=False).encode(),
                       file_name=f"failed_records_{pid[:8]}.csv", mime="text/csv")

    st.divider()
    st.markdown("**Fix the data, then revalidate:**")
    failing_ids = sorted(detail["Test case"].unique())
    if st.button(f"🔁 Rerun the {len(failing_ids)} failing test case(s)", type="primary"):
        eng = get_regression_engine()
        with st.spinner("Rerunning…"):
            outcome = eng.rerun_selected(failing_ids, batch_id=batch_id(), project_id=pid,
                                         triggered_from="EXECUTION_HISTORY")
        if outcome.all_passed:
            st.success(f"All {outcome.total} now pass. {outcome.summary()}")
        else:
            st.error(outcome.summary())
        show_results(outcome.results, eng.history)

# ------------------------------------------------------------------- by folder
with st.expander("Breakdown by folder"):
    if latest:
        by_folder = pd.DataFrame(latest).groupby(["FOLDER_PATH", "STATUS"]).size() \
            .unstack(fill_value=0)
        st.dataframe(by_folder, use_container_width=True)
