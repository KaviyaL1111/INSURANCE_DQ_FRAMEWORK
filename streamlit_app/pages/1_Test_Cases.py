"""Browse a folder, author and edit saved test cases, and run them."""
import pandas as pd
import streamlit as st

from common import (batch_id, flatfile_tables_hint, fmt_ts, folder_options, get_engine,
                    get_history, get_repository, page_header, page_setup, param_inputs,
                    show_results, sidebar, status_label)
from src.config import available_connections
from src.repository import (DuplicateNameError, SEVERITIES, TEST_TYPES,
                            VALIDATION_SCOPES, TestCase)

TYPE_HELP = {
    "SQL_ROWCOUNT": "A rule query. It PASSES when it returns no rows — every row it "
                    "returns is a failed record.",
    "SOURCE_TARGET_COMPARE": "Runs a source query and a target query, matches rows on the "
                             "key column(s), and reports per-column expected vs actual.",
    "ROW_COUNT_MATCH": "Compares how many rows the source and target queries return.",
    "STATUS_COLUMN": "The query returns a STATUS column; any row that is not PASS fails.",
    "INFORMATIONAL": "Always passes. Use it for reports you want captured in history.",
}

page_setup("Test Cases", "🧪")
project = sidebar()
repo = get_repository()
pid = project["PROJECT_ID"]

page_header("🧪 Test Cases", "Author, version and execute saved validations. Source and "
            "target can each be Snowflake, MSSQL or a flat file.")

folders = folder_options(repo, pid)
if not folders:
    st.info("This project has no folders yet. Create one on the **Projects & Folders** page.")
    st.stop()

label = st.selectbox("Folder", list(folders), key="tc_folder")
folder_id = folders[label]
folder = repo.get_folder(folder_id)
rows = repo.list_test_cases(folder_id=folder_id)

browse, editor = st.tabs([f"Browse ({len(rows)})", "Create / Edit"])

# ------------------------------------------------------------------ browse
with browse:
    st.caption(f"Folder `{folder['FOLDER_PATH']}`")
    if not rows:
        st.info("No test cases in this folder yet — add one on the **Create / Edit** tab.")
    else:
        last = {h["TEST_CASE_ID"]: h for h in get_history().get_execution_history(
            "1970-01-01", "2999-12-31", project_id=pid, latest_per_test=True)}

        def _connections(r) -> str:
            src, tgt = r["SOURCE_CONNECTION"], r["TARGET_CONNECTION"]
            return src if r["TEST_TYPE"] not in ("SOURCE_TARGET_COMPARE", "ROW_COUNT_MATCH") \
                else f"{src} → {tgt}"

        query = st.text_input("Search", placeholder="Filter by ID, name, type or entity",
                              key="tc_search", label_visibility="collapsed")
        q = query.strip().lower()
        shown = [r for r in rows if not q or q in " ".join(
            str(r[k] or "") for k in ("TEST_CASE_ID", "TEST_NAME", "TEST_TYPE", "ENTITY")).lower()]

        table = pd.DataFrame([{
            "Last result": status_label(last[r["TEST_CASE_ID"]]["STATUS"])
            if r["TEST_CASE_ID"] in last else "⚪ not run",
            "ID": r["TEST_CASE_ID"], "Name": r["TEST_NAME"], "Type": r["TEST_TYPE"],
            "Connections": _connections(r), "Entity": r["ENTITY"],
            "Severity": r["SEVERITY"], "Regression": bool(r["IS_REGRESSION"]),
            "v": r["VERSION_NO"],
            "Last run (IST)": fmt_ts(last[r["TEST_CASE_ID"]]["RUN_TS"])
            if r["TEST_CASE_ID"] in last else "",
        } for r in shown])
        event = st.dataframe(
            table, use_container_width=True, hide_index=True, on_select="rerun",
            selection_mode="multi-row", key=f"tc_table_{folder_id}",
            column_config={"Regression": st.column_config.CheckboxColumn("Regression")})
        ids = [shown[i]["TEST_CASE_ID"] for i in event.selection.rows]
        if q and not shown:
            st.caption(f"No test case in this folder matches “{query.strip()}”.")
        st.caption(f"{len(ids)} selected — tick rows in the table to choose what to execute."
                   if ids else "Tick rows in the table to choose what to execute.")

        overrides = {}
        if ids:
            needed, defaults = [], {}
            for tc in repo.get_test_cases(ids):
                defaults.update(tc.param_defaults or {})
                for p in tc.required_params():
                    if p not in needed:
                        needed.append(p)
            overrides = param_inputs(needed, defaults, "browse")

        c1, c2 = st.columns([1, 3])
        run_selected = c1.button(f"▶ Execute {len(ids) or ''} selected".replace("  ", " "),
                                 type="primary",
                                 disabled=not ids, use_container_width=True)
        run_folder = c2.button(f"▶ Execute the whole {folder['FOLDER_NAME']} folder",
                               use_container_width=True)

        if run_selected or run_folder:
            engine = get_engine()
            with st.spinner("Running…"):
                if run_folder:
                    results = engine.run_folder(folder_id, params=overrides,
                                                batch_id=batch_id())
                else:
                    results = engine.run_test_cases(repo.get_test_cases(ids), params=overrides,
                                                    batch_id=batch_id(), run_mode="ADHOC")
            show_results(results, engine.history)

# ------------------------------------------------------------------ editor
with editor:
    existing = ["(new test case)"] + [f"{r['TEST_CASE_ID']} · {r['TEST_NAME']}" for r in rows]
    choice = st.selectbox("Editing", existing, key="tc_edit_pick")
    is_new = choice == "(new test case)"
    tc = TestCase(folder_id=folder_id, project_id=pid) if is_new \
        else repo.get_test_case(choice.split(" · ")[0])

    if not is_new:
        versions = repo.test_case_versions(tc.test_case_id)
        st.caption(f"`{tc.test_case_id}` · version {tc.version_no} · "
                   f"last edited {fmt_ts(tc.updated_ts)} IST by {tc.updated_by}")

    flatfile_tables_hint()

    with st.form("test_case_form"):
        c1, c2 = st.columns([3, 2])
        name = c1.text_input("Test case name *", value=tc.test_name,
                             help="Must be unique within this folder.")
        target_folder_label = c2.selectbox(
            "Folder", list(folders),
            index=list(folders.values()).index(tc.folder_id) if tc.folder_id in folders.values() else 0)
        description = st.text_area("Description", value=tc.description, height=70)

        c1, c2, c3, c4 = st.columns(4)
        test_type = c1.selectbox("Test type *", TEST_TYPES, index=TEST_TYPES.index(tc.test_type))
        scope = c2.selectbox("Validation scope", VALIDATION_SCOPES,
                             index=VALIDATION_SCOPES.index(tc.validation_scope)
                             if tc.validation_scope in VALIDATION_SCOPES else 3)
        severity = c3.selectbox("Severity", SEVERITIES,
                                index=SEVERITIES.index(tc.severity)
                                if tc.severity in SEVERITIES else 2)
        entity = c4.text_input("Entity", value=tc.entity, placeholder="Policy / Claim / Customer")
        st.caption(TYPE_HELP[test_type])

        profiles = available_connections() or ["snowflake"]
        needs_two = test_type in ("SOURCE_TARGET_COMPARE", "ROW_COUNT_MATCH")

        if needs_two:
            c1, c2 = st.columns(2)
            src_conn = c1.selectbox("Source connection", profiles,
                                    index=profiles.index(tc.source_connection)
                                    if tc.source_connection in profiles else 0)
            tgt_conn = c2.selectbox("Target connection", profiles,
                                    index=profiles.index(tc.target_connection)
                                    if tc.target_connection in profiles else 0,
                                    help="Pick a different connection here to compare "
                                         "Snowflake against MSSQL, or a flat file against "
                                         "the table it was loaded into.")
            source_sql = st.text_area("Source SQL *", value=tc.source_sql, height=170,
                                      help="Use :batch_id and any other :parameters you need.")
            target_sql = st.text_area("Target SQL *", value=tc.target_sql, height=170)
            validation_sql = tc.validation_sql
            if test_type == "SOURCE_TARGET_COMPARE":
                c1, c2, c3 = st.columns([1, 2, 1])
                key_columns = c1.text_input("Key column(s) *", value=tc.key_columns,
                                            placeholder="POLICY_ID",
                                            help="Comma-separated business key.")
                compare_columns = c2.text_input("Compare columns", value=tc.compare_columns,
                                                placeholder="blank = every shared column")
                tolerance = c3.number_input("Numeric tolerance", value=float(tc.tolerance),
                                            step=0.01, format="%.4f")
            else:
                key_columns, compare_columns = tc.key_columns, tc.compare_columns
                tolerance = st.number_input("Row-count tolerance", value=float(tc.tolerance),
                                            step=1.0)
        else:
            src_conn = st.selectbox("Connection", profiles,
                                    index=profiles.index(tc.source_connection)
                                    if tc.source_connection in profiles else 0)
            tgt_conn = src_conn
            validation_sql = st.text_area(
                "Validation SQL *", value=tc.validation_sql, height=240,
                help="Name columns BUSINESS_KEY, COLUMN_NAME, EXPECTED_VALUE, ACTUAL_VALUE "
                     "and FAILURE_REASON and they will populate the mismatch detail directly.")
            source_sql = target_sql = ""
            key_columns = compare_columns = ""
            tolerance = 0.0

        c1, c2 = st.columns(2)
        is_regression = c1.checkbox("Include in regression suite", value=tc.is_regression)
        change_note = c2.text_input("Change note", value="" if is_new else "Edited",
                                    disabled=is_new)

        saved = st.form_submit_button("💾 Save test case", type="primary")

    if saved:
        candidate = TestCase(
            test_case_id=tc.test_case_id, project_id=pid,
            folder_id=folders[target_folder_label], test_name=name, description=description,
            test_type=test_type, validation_scope=scope, entity=entity,
            layer=tc.layer, severity=severity,
            source_connection=src_conn, target_connection=tgt_conn,
            source_sql=source_sql, target_sql=target_sql, validation_sql=validation_sql,
            key_columns=key_columns, compare_columns=compare_columns,
            param_defaults=tc.param_defaults, tolerance=float(tolerance),
            is_regression=is_regression, is_active=True, version_no=tc.version_no)
        try:
            if is_new:
                created = repo.create_test_case(candidate)
                st.success(f"Saved as **{created.test_case_id}**.")
            else:
                updated = repo.update_test_case(candidate, change_note=change_note or "Edited")
                st.success(f"Saved — now version {updated.version_no}.")
            st.rerun()
        except (DuplicateNameError, ValueError) as exc:
            st.error(str(exc))

    if not is_new:
        st.divider()
        a, b, c = st.columns(3)

        with a.popover("📋 Copy to another folder", use_container_width=True):
            dest = st.selectbox("Destination", list(folders), key="copy_dest")
            new_name = st.text_input("Name for the copy",
                                     value=f"[Regression] {tc.test_name}", key="copy_name")
            if st.button("Create the copy", key="do_copy"):
                try:
                    copy = repo.copy_test_case(tc.test_case_id, folders[dest], new_name)
                    st.success(f"Created {copy.test_case_id} in {dest.strip()}.")
                    st.rerun()
                except (DuplicateNameError, ValueError) as exc:
                    st.error(str(exc))

        with b.popover("🕘 Version history", use_container_width=True):
            vs = repo.test_case_versions(tc.test_case_id)
            st.dataframe(pd.DataFrame(vs), use_container_width=True, hide_index=True)
            restore_to = st.number_input("Restore version", min_value=1,
                                         max_value=max(v["VERSION_NO"] for v in vs) if vs else 1,
                                         value=1, key="restore_v")
            if st.button("Restore", key="do_restore"):
                repo.restore_version(tc.test_case_id, int(restore_to))
                st.success(f"Restored version {int(restore_to)}.")
                st.rerun()

        with c.popover("🗑️ Delete", use_container_width=True):
            st.warning("The test case is hidden from listings. Its execution history is kept.")
            if st.button("Delete this test case", key="do_delete"):
                repo.delete_test_case(tc.test_case_id)
                st.success("Deleted.")
                st.rerun()

        with st.expander("▶ Test run (does not write to execution history)"):
            overrides = param_inputs(tc.required_params(), tc.param_defaults, "preview")
            if st.button("Run preview"):
                engine = get_engine()
                params = engine.resolve_params(tc, overrides, batch_id())
                try:
                    if tc.test_type in ("SOURCE_TARGET_COMPARE", "ROW_COUNT_MATCH"):
                        for title, sql, conn in (("Source", tc.source_sql, tc.source_connection),
                                                 ("Target", tc.target_sql, tc.target_connection)):
                            cols, data, n = engine.preview(sql, conn, params)
                            st.caption(f"{title} — {n} row(s)")
                            st.dataframe(pd.DataFrame(data, columns=cols),
                                         use_container_width=True, hide_index=True)
                    else:
                        cols, data, n = engine.preview(tc.validation_sql, tc.source_connection, params)
                        st.caption(f"{n} row(s) returned — "
                                   f"{'PASS' if n == 0 else 'these would be the failed records'}")
                        if data:
                            st.dataframe(pd.DataFrame(data, columns=cols),
                                         use_container_width=True, hide_index=True)
                except Exception as exc:
                    st.error(f"{type(exc).__name__}: {exc}")
