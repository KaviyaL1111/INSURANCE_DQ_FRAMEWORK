"""
Demo pipeline controls.

Drives the insurance sample pipeline so the whole brief can be walked
through from the browser: load source and target, introduce mismatches,
validate, correct, rerun from the Regression folder.
"""
import pandas as pd
import streamlit as st

from common import (batch_id, get_regression_engine, get_repository, page_setup,
                    show_results, sidebar)
from src import etl_loader
from src.seed import seed_catalog

page_setup("Demo Pipeline", "⚙️")
project = sidebar(require_project=False)

st.title("Demo Pipeline")
st.caption("Everything here operates on the sample insurance dataset "
           "(30 customers, 40 policies, 50 claims). Use it to walk through the "
           "corrupt → detect → correct → rerun cycle.")

st.subheader("1 · Set up")
c1, c2 = st.columns(2)
with c1:
    if st.button("Create tables", use_container_width=True,
                 help="Repository control tables plus the insurance demo tables."):
        with st.spinner("Creating…"):
            etl_loader.init_schema()
        st.success("Tables created.")
with c2:
    if st.button("Seed project & test cases", use_container_width=True):
        with st.spinner("Seeding…"):
            summary = seed_catalog()
        st.success(f"'{summary['project']}': {summary['created']} created, "
                   f"{summary['updated']} updated, "
                   f"{summary['regression_copies']} regression copies.")
        st.rerun()

st.divider()
st.subheader("2 · Load source → target")
c1, c2 = st.columns(2)
if c1.button("Load with 3 intentional mismatches", type="primary", use_container_width=True):
    with st.spinner("Loading…"):
        result = etl_loader.run_full_pipeline(batch_id=batch_id(), inject_mismatches=True)
    st.success(f"Loaded batch {result['batch_id']} with defects injected.")
    st.dataframe(pd.DataFrame([result["row_counts"]]), use_container_width=True, hide_index=True)
    st.caption("Injected: P1005 premium 12000→10000 · CL1007 status Approved→Pending · "
               "CL1010 re-pointed at a non-existent policy P9999.")
if c2.button("Load clean (no mismatches)", use_container_width=True):
    with st.spinner("Loading…"):
        result = etl_loader.run_full_pipeline(batch_id=batch_id(), inject_mismatches=False)
    st.success(f"Loaded batch {result['batch_id']} cleanly.")
    st.dataframe(pd.DataFrame([result["row_counts"]]), use_container_width=True, hide_index=True)

if project is None:
    st.info("Seed the project above to continue.")
    st.stop()

pid = project["PROJECT_ID"]

st.divider()
st.subheader("3 · Validate")
if st.button("▶ Run the full validation suite", type="primary"):
    eng = get_regression_engine()
    progress = st.progress(0.0)
    outcome = eng.run_full_suite(
        pid, batch_id=batch_id(),
        on_progress=lambda d, t, r: progress.progress(d / t, text=f"{d}/{t} · {r.test_case_id}"))
    progress.empty()
    st.session_state["demo_first_run"] = outcome.summary()
    show_results(outcome.results, eng.history)

st.divider()
st.subheader("4 · Correct the data")
c1, c2 = st.columns(2)
if c1.button("Apply corrections", type="primary", use_container_width=True):
    with st.spinner("Correcting…"):
        etl_loader.fix_mismatches(batch_id())
    st.success("Corrected from the transformation layer.")
if c2.button("Re-inject the defects", use_container_width=True):
    etl_loader.introduce_mismatches(batch_id())
    st.success("Defects re-injected.")

st.divider()
st.subheader("5 · Rerun the saved regression scripts")
folder_path = st.text_input("Regression folder", value="/Regression")
if st.button("🔁 Rerun from the Regression folder", type="primary"):
    eng = get_regression_engine()
    try:
        outcome = eng.run_regression_folder(pid, folder_path, batch_id=batch_id())
    except LookupError as exc:
        st.error(str(exc))
    else:
        if outcome.all_passed:
            st.success(f"Regression complete — all {outcome.total} test case(s) pass. "
                       f"{outcome.summary()}")
            st.balloons()
        else:
            st.error(outcome.summary())
        show_results(outcome.results, eng.history)

if st.session_state.get("demo_first_run"):
    st.caption(f"Earlier run in this session: {st.session_state['demo_first_run']}")
