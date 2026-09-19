"""

Insurance DQ & Regression Automation — home / 🛡️ Enterprise Data Quality Platform

 

    streamlit run streamlit_app/app.py

"""

import pandas as pd

import streamlit as st

 

from common import (get_history, get_repository, page_setup, sidebar)

from src.repository import DuplicateNameError

 

page_setup("🛡️ Enterprise Data Quality Platform")

project = sidebar(require_project=False)

repo = get_repository()

 

st.title("🛡️ Enterprise Data Quality Platform")

st.caption("Create an application or project, then organise saved validation "

           "test cases into a folder structure of your own design.")

 

# ---------------------------------------------------------------- projects

left, right = st.columns([3, 2], gap="large")

 

with left:

    st.subheader("Applications / Projects")

    projects = repo.list_projects()

    if projects:

        st.dataframe(

            pd.DataFrame([{"Project": p["PROJECT_NAME"],

                           "Description": p["DESCRIPTION"] or "",

                           "Created by": p["CREATED_BY"],

                           "Created": p["CREATED_TS"]} for p in projects]),

            use_container_width=True, hide_index=True)

    else:

        st.info("No projects yet — create your first one on the right.")

 

with right:

    st.subheader("New project")

    with st.form("new_project", clear_on_submit=True):

        name = st.text_input("Project name", placeholder="e.g. Motor Claims Migration")

        description = st.text_area("Description", height=80)

        if st.form_submit_button("Create project", type="primary", use_container_width=True):

            if not name.strip():

                st.error("A project needs a name.")

            else:

                try:

                    repo.create_project(name, description)

                    st.success(f"Created '{name}'.")

                    st.session_state["project_name"] = name.strip()

                    st.rerun()

                except DuplicateNameError as exc:

                    st.error(str(exc))

 

if project is None:

    st.stop()

 

st.divider()

 

# ----------------------------------------------------------------- folders

st.subheader(f"Folder structure — {project['PROJECT_NAME']}")

pid = project["PROJECT_ID"]

tree = repo.folder_tree(pid)

 

tree_col, form_col = st.columns([3, 2], gap="large")

 

with tree_col:

    if tree:

        for f in tree:

            depth = f["FOLDER_PATH"].count("/") - 1

            icon = "🔁" if f["FOLDER_TYPE"] == "REGRESSION" else "📁"

            count = f["TEST_COUNT"]

            st.markdown(

                f"<div style='padding:2px 0 2px {depth * 24}px'>{icon} <b>{f['FOLDER_NAME']}</b>"

                f"<span style='color:#888'> · {count} test case{'s' if count != 1 else ''}"

                f" · <code>{f['FOLDER_PATH']}</code></span></div>",

                unsafe_allow_html=True)

    else:

        st.info("No folders yet. Create one on the right — for example *Staging*, "

                "*Source-to-Target* and *Regression*.")

 

with form_col:

    st.markdown("**New folder**")

    parents = {"(top level)": None}

    parents.update({f["FOLDER_PATH"]: f["FOLDER_ID"] for f in tree})

    with st.form("new_folder", clear_on_submit=True):

        fname = st.text_input("Folder name", placeholder="e.g. Source-to-Target")

        parent_label = st.selectbox("Inside", list(parents))

        ftype = st.selectbox("Type", ["STANDARD", "REGRESSION"],

                             help="A REGRESSION folder is where corrected defects are "

                                  "re-validated from. It behaves the same, but is "

                                  "highlighted and is the default target of "

                                  "'Run regression'.")

        if st.form_submit_button("Create folder", type="primary", use_container_width=True):

            if not fname.strip():

                st.error("A folder needs a name.")

            else:

                try:

                    repo.create_folder(pid, fname, parent_folder_id=parents[parent_label],

                                       folder_type=ftype)

                    st.success(f"Created '{fname}'.")

                    st.rerun()

                except (DuplicateNameError, ValueError) as exc:

                    st.error(str(exc))

 

    if tree:

        with st.expander("Rename or delete a folder"):

            target = st.selectbox("Folder", [f["FOLDER_PATH"] for f in tree], key="mod_folder")

            fid = next(f["FOLDER_ID"] for f in tree if f["FOLDER_PATH"] == target)

            new_name = st.text_input("New name", key="rename_to")

            c1, c2 = st.columns(2)

            if c1.button("Rename", use_container_width=True):

                try:

                    repo.rename_folder(fid, new_name)

                    st.success("Renamed.")

                    st.rerun()

                except (DuplicateNameError, ValueError) as exc:

                    st.error(str(exc))

            if c2.button("Delete", use_container_width=True,

                         help="Soft delete — the folder and its test cases are hidden, "

                              "and execution history is preserved."):

                repo.delete_folder(fid)

                st.success("Deleted.")

                st.rerun()

 

# ------------------------------------------------------------------ status

st.divider()

summary = get_history().dashboard_summary(pid)

c1, c2, c3, c4 = st.columns(4)

c1.metric("Test cases", len(repo.list_test_cases(project_id=pid)))

c2.metric("Executed at least once", summary["total"])

c3.metric("Currently failing", summary["failed"])

c4.metric("Pass rate", f"{summary['pass_pct']}%" if summary["pass_pct"] is not None else "—")

 

st.caption("Next: **Test Cases** to author validations · **Execution History** to rerun "

           "past runs · **Dashboard** for the current state.")