"""Current data-quality state: pass/fail from each test case's latest run."""
import pandas as pd
import streamlit as st

from common import (batch_id, get_history, get_regression_engine, get_repository,
                    page_setup, show_results, sidebar, STATUS_ICON)

page_setup("Dashboard", "📊")
project = sidebar()
repo = get_repository()
history = get_history()
pid = project["PROJECT_ID"]

st.title("Dashboard")
st.caption("Every figure below reflects the **most recent run of each test case**, so a "
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

if summary["errored"]:
    st.warning(f"{summary['errored']} test case(s) could not run at all — check their SQL.")

st.progress((summary["passed"] / summary["total"]) if summary["total"] else 0.0)
st.divider()

# ------------------------------------------------------------ latest status
st.subheader("Latest status by test case")
latest = history.get_execution_history("1970-01-01", "2999-12-31",
                                       project_id=pid, latest_per_test=True)
if latest:
    st.dataframe(
        pd.DataFrame([{
            "": STATUS_ICON.get(r["STATUS"], "⚪"),
            "Test case": r["TEST_CASE_ID"],
            "Name": r["TEST_NAME"],
            "Folder": r["FOLDER_PATH"],
            "Status": r["STATUS"],
            "Failed records": r["FAILED_RECORD_COUNT"],
            "Last run": r["RUN_TS"],
            "Version": r["TEST_VERSION_NO"],
        } for r in latest]),
        use_container_width=True, hide_index=True)

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
        "When": f["FAILED_TS"],
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
