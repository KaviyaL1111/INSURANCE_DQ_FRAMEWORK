"""Current data-quality state: pass/fail from each test case's latest run."""
from datetime import timedelta

import pandas as pd
import streamlit as st

import charts
from common import (batch_id, fmt_ts, get_history, get_regression_engine, get_repository,
                    page_header, page_setup, show_results, sidebar, status_label)
from src.config import today_ist
from src.etl_loader import record_counts


@st.cache_data(show_spinner="Counting records…", ttl=300)
def _record_counts(connection: str, batch: str, nonce: int):
    """Cached for five minutes, or until Refresh is pressed."""
    return record_counts(get_repository().db, batch)


def _chart(chart) -> None:
    st.altair_chart(chart, use_container_width=True, theme="streamlit")


page_setup("Dashboard", "📊")
project = sidebar()
repo = get_repository()
history = get_history()
pid = project["PROJECT_ID"]

page_header("📊 Dashboard",
            "Every figure below reflects the **most recent run of each test case**, so a "
            "corrected defect stops being counted the moment it is revalidated.")

# ------------------------------------------------------------------- KPI row
summary = history.dashboard_summary(pid)
total = summary["total"]
st.html(charts.stat_tiles_html([
    {"label": "Test cases run", "value": f"{total:,}", "tone": "accent",
     "note": "latest run of each"},
    {"label": "Passed", "value": f"{summary['passed']:,}", "tone": "PASS",
     "note": f"of {total:,} tests" if total else ""},
    {"label": "Failed", "value": f"{summary['failed']:,}", "tone": "FAIL",
     "note": "need attention" if summary["failed"] else "none failing"},
    {"label": "Could not run", "value": f"{summary['errored']:,}", "tone": "ERROR",
     "note": "SQL or connection errors" if summary["errored"] else "all ran"},
    {"label": "Pass rate", "value": f"{summary['pass_pct']}%" if summary["pass_pct"] is not None
     else "—", "tone": "accent", "note": "across the project"},
    {"label": "Failed records", "value": f"{summary['failed_records'] or 0:,}", "tone": "FAIL",
     "note": "mismatches to fix"},
]))

latest = history.get_execution_history("1970-01-01", "2999-12-31",
                                       project_id=pid, latest_per_test=True) if total else []
failures = history.latest_failures_for_project(pid, limit=500) if total else []

# ------------------------------------------------------------- status charts
if latest:
    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("**Latest result by folder**")
        _chart(charts.status_by_folder(latest))
    with right:
        if failures:
            by = st.segmented_control("Failed records by", ["Failure type", "Column", "Test case"],
                                      default="Failure type", key="dash_fail_by") or "Failure type"
            field = {"Failure type": "FAILURE_TYPE", "Column": "COLUMN_NAME",
                     "Test case": "TEST_CASE_ID"}[by]
            _chart(charts.failures_by(failures, field, by))
        else:
            st.markdown("**Failed records**")
            st.success("No failing records in the latest run of any test case. 🎉")

    # ------------------------------------------------------------ trend charts
    st.markdown("#### Trend")
    days = st.segmented_control("Period", ["7 days", "30 days", "90 days"], default="30 days",
                                key="dash_period", label_visibility="collapsed") or "30 days"
    end = today_ist()
    start = end - timedelta(days=int(days.split()[0]) - 1)
    runs = history.get_execution_history(start, end, project_id=pid)
    if runs:
        left, right = st.columns(2, gap="large")
        with left:
            st.markdown("**Executions per day**")
            _chart(charts.runs_over_time(runs))
        with right:
            st.markdown("**Pass rate per day**")
            _chart(charts.pass_rate_trend(runs))
    else:
        st.caption(f"No executions in the last {days}.")

# ------------------------------------------------------ records in the database
st.divider()
head, refresh = st.columns([5, 1])
head.subheader("Records in the database")
if refresh.button("↻ Refresh", key="dash_counts_refresh", use_container_width=True):
    st.session_state["dash_counts_nonce"] = st.session_state.get("dash_counts_nonce", 0) + 1
try:
    counts = _record_counts(repo.db.profile.name, batch_id(),
                            st.session_state.get("dash_counts_nonce", 0))
except Exception as exc:
    st.warning("Could not count the pipeline tables — they may not have been created yet "
               "(**Create tables** on the Demo Pipeline page, or `python -m src.cli init`)."
               f"\n\n`{type(exc).__name__}: {str(exc)[:200]}`")
else:
    tiles = []
    for layer in charts.LAYER_ORDER:
        rows = [c for c in counts if c["layer"] == layer]
        tiles.append({"label": f"{layer} records", "value": f"{sum(c['total'] for c in rows):,}",
                      "tone": "accent", "note": f"{sum(c['batch'] for c in rows):,} in batch "
                                            f"{batch_id()}"})
    tiles.append({"label": "All pipeline tables", "value": f"{sum(c['total'] for c in counts):,}",
                  "tone": "neutral", "note": f"{len(counts)} tables"})
    st.html(charts.stat_tiles_html(tiles))

    chart_tab, table_tab = st.tabs(["📊 Chart", "📋 Table"])
    with chart_tab:
        _chart(charts.records_by_layer(counts))
    with table_tab:
        st.dataframe(pd.DataFrame([{
            "Layer": c["layer"],
            "Table": c["table"],
            "Total records": c["total"],
            f"In batch {batch_id()}": c["batch"],
        } for c in counts]), use_container_width=True, hide_index=True)
    st.caption(f"{repo.db.profile.name} database · batch from the sidebar. Curated tables hold "
               "one row per key across all batches, so their batch count is the keys that "
               "batch delivered.")

if total == 0:
    st.info("Nothing has been executed yet. Run some test cases from the **Test Cases** page.")
    st.stop()

st.divider()

# ------------------------------------------------------------ latest status
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
