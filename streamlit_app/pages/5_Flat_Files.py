"""
Flat-file connectivity.

Browse the CSV / Excel / JSON / Parquet files behind the `flatfile`
connection, upload new ones, query them with SQL, and turn a file into a
saved file-vs-table reconciliation test case in a few clicks.
"""
import os

import pandas as pd
import streamlit as st

from common import (batch_id, flatfile_available, flatfile_dir, flatfile_signature,
                    get_engine, show_results,
                    folder_options, get_flatfile, get_repository, page_header, page_setup,
                    param_inputs, sidebar)
from src.config import available_connections
from src.connectors.base import extract_params
from src.connectors.flatfile_connector import SUPPORTED_EXTENSIONS, safe_filename, table_name_for
from src.repository import DuplicateNameError, SEVERITIES, TestCase
from src.staging import (STAGING_TABLES, check_for_staging, guess_staging_table,
                         load_to_staging, read_file_as_text)

PREVIEW_ROWS = 200
_ISO_DATE = r"^\d{4}-\d{2}-\d{2}$"
_ISO_TS = r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$"


def _kind(s: pd.Series) -> str:
    """What a column holds, in words an analyst uses rather than pandas dtypes."""
    values = s.dropna()
    if values.empty:
        return "empty"
    if pd.api.types.is_bool_dtype(values):
        return "boolean"
    if pd.api.types.is_integer_dtype(values):
        return "integer"
    if pd.api.types.is_float_dtype(values):
        return "integer" if (values == values.round()).all() else "decimal"
    text = values.astype(str)
    if text.str.match(_ISO_DATE).all():
        return "date"
    if text.str.match(_ISO_TS).all():
        return "timestamp"
    return "text"


@st.cache_data(show_spinner="Profiling…", max_entries=32)
def _profile(signature: tuple, table: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(preview rows, column profile) — cached until a file in the folder changes."""
    df = get_flatfile().query(f'SELECT * FROM "{table}"').to_frame()
    n = len(df)
    prof = pd.DataFrame([{
        "Column": c,
        "Type": _kind(df[c]),
        "Nulls": int(df[c].isna().sum()),
        "Null %": round(df[c].isna().mean() * 100, 1) if n else 0.0,
        "Distinct": int(df[c].nunique(dropna=True)),
        "Unique key?": bool(n) and bool(df[c].notna().all()) and bool(df[c].is_unique),
        "Min": _fmt(df[c].dropna().min()) if df[c].notna().any() and _kind(df[c]) != "text" else "",
        "Max": _fmt(df[c].dropna().max()) if df[c].notna().any() and _kind(df[c]) != "text" else "",
        "Example": _fmt(df[c].dropna().iloc[0]) if df[c].notna().any() else "",
    } for c in df.columns])
    return df.head(PREVIEW_ROWS), prof


def _fmt(v) -> str:
    return "" if v is None else str(v)[:60]

page_setup("Flat Files", "📄")
project = sidebar(require_project=False)

page_header("📄 Flat Files",
            "Every supported file in the flat-file folder is exposed as a table on the "
            "`flatfile` connection. Point any test case's source or target at it to validate "
            "a landed file against the database table it was loaded into.")

if not flatfile_available():
    st.error("The flat-file connection is not configured. Set `FLATFILE_DIR` in `.env` to an "
             "existing folder (it defaults to the project's `data/` folder).")
    st.stop()

folder = flatfile_dir()
try:
    ff = get_flatfile()
    tables = ff.describe()
except Exception as exc:
    st.error(f"Could not read `{folder}`: {type(exc).__name__}: {exc}")
    st.stop()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Files", len({t["file"] for t in tables}) + len(ff.load_errors))
c2.metric("Tables", len(tables))
c3.metric("Total rows", f"{sum(t['rows'] for t in tables):,}")
c4.metric("Unreadable files", len(ff.load_errors))
st.caption(f"Folder: `{folder}`")

for err in ff.load_errors:
    st.warning(f"**{err['file']}** could not be loaded — {err['error']}")

# ------------------------------------------------------------------ upload
with st.expander("⬆ Upload files", expanded=not tables):
    uploads = st.file_uploader(
        "Drop files here", accept_multiple_files=True,
        type=[e.lstrip(".") for e in SUPPORTED_EXTENSIONS],
        help="Each file becomes a table named after the file: claims_2026.csv → CLAIMS_2026. "
             "An Excel workbook with several sheets becomes one table per sheet.")
    if uploads:
        plan = []
        for up in uploads:
            try:
                name = safe_filename(up.name)
                plan.append({"File": up.name, "Saved as": name,
                             "Table": table_name_for(name),
                             "Size": f"{up.size / 1024:,.1f} KB",
                             "Note": "replaces existing file"
                             if os.path.exists(os.path.join(folder, name)) else "new"})
            except ValueError as exc:
                plan.append({"File": up.name, "Saved as": "", "Table": "", "Size": "",
                             "Note": str(exc)})
        st.dataframe(pd.DataFrame(plan), use_container_width=True, hide_index=True)
    overwrite = st.checkbox("Replace files that already exist", value=False)
    if uploads and st.button("Save to the flat-file folder", type="primary"):
        saved, skipped = [], []
        for up in uploads:
            try:
                name = safe_filename(up.name)
            except ValueError as exc:
                skipped.append(str(exc))
                continue
            dest = os.path.join(folder, name)
            if os.path.exists(dest) and not overwrite:
                skipped.append(f"{name} already exists (tick *Replace* to overwrite)")
                continue
            with open(dest, "wb") as f:
                f.write(up.getbuffer())
            saved.append(name)
        for msg in skipped:
            st.warning(msg)
        if saved:
            st.toast(f"Saved {', '.join(saved)}", icon="✅")
            st.rerun()

if not tables:
    st.info(f"No supported files yet ({', '.join(SUPPORTED_EXTENSIONS)}). Upload one above.")
    st.stop()

browse_tab, sql_tab, load_tab, build_tab = st.tabs(
    ["🔎 Browse & profile", "🧮 SQL query", "⬆ Load into staging",
     "🧪 Create a reconciliation test"])

by_name = {t["table"]: t for t in tables}


def _table_label(name: str) -> str:
    t = by_name[name]
    sheet = f" / {t['sheet']}" if t["sheet"] else ""
    return f"{name}  —  {t['file']}{sheet} · {t['rows']:,} rows"


# ------------------------------------------------------------------ browse
with browse_tab:
    overview = pd.DataFrame([{"Table": t["table"], "File": t["file"], "Sheet": t["sheet"],
                              "Rows": t["rows"], "Columns": len(t["columns"])}
                             for t in tables])
    st.dataframe(overview, use_container_width=True, hide_index=True)

    chosen = st.selectbox("Table", list(by_name), format_func=_table_label, key="ff_table")
    preview_df, prof = _profile(flatfile_signature(), chosen)
    total_rows = by_name[chosen]["rows"]

    keys = prof.loc[prof["Unique key?"], "Column"].tolist()
    nullable = int((prof["Nulls"] > 0).sum())
    c1, c2, c3 = st.columns(3)
    c1.metric("Rows", f"{total_rows:,}")
    c2.metric("Columns with nulls", f"{nullable} of {len(prof)}")
    c3.metric("Candidate keys", len(keys), help=", ".join(keys) or "No column is unique and non-null.")

    profile_tab, preview_tab = st.tabs(["Column profile", f"Preview ({min(PREVIEW_ROWS, total_rows):,} "
                                                           f"of {total_rows:,} rows)"])
    with profile_tab:
        st.dataframe(prof, use_container_width=True, hide_index=True,
                     column_config={
                         "Null %": st.column_config.ProgressColumn(
                             "Null %", min_value=0, max_value=100, format="%.1f%%"),
                         "Unique key?": st.column_config.CheckboxColumn(
                             "Unique key?", help="Every value present and different — "
                                                 "usable as a reconciliation key."),
                     })
    with preview_tab:
        st.dataframe(preview_df, use_container_width=True, hide_index=True)

    with st.popover("🗑️ Delete this file"):
        victim = by_name[chosen]["file"]
        st.warning(f"This permanently removes **{victim}** from `{folder}`. Test cases that "
                   "query its table will start to error.")
        if st.button(f"Delete {victim}", type="primary", key="ff_delete"):
            os.remove(os.path.join(folder, victim))
            st.toast(f"Deleted {victim}", icon="🗑️")
            st.rerun()

# --------------------------------------------------------------------- SQL
with sql_tab:
    st.caption("SQLite dialect. Tables are listed on the *Browse* tab; `:named` parameters "
               "work exactly as in saved test cases.")
    sql = st.text_area("SQL", key="ff_sql", height=160,
                       value=f'SELECT *\nFROM {tables[0]["table"]}\nLIMIT 100')
    needed = extract_params(sql)
    params = param_inputs(needed, {}, "ff_sql")
    if "batch_id" in needed:
        params["batch_id"] = batch_id()
    if st.button("▶ Run query", type="primary"):
        try:
            res = ff.query(sql, params)
        except Exception as exc:
            st.error(f"{type(exc).__name__}: {exc}")
        else:
            out = res.to_frame()
            st.caption(f"{res.row_count:,} row(s)")
            st.dataframe(out, use_container_width=True, hide_index=True)
            st.download_button("⬇ Download (CSV)", out.to_csv(index=False).encode(),
                               file_name="flatfile_query.csv", mime="text/csv")

# ------------------------------------------------------- load into staging
@st.cache_data(show_spinner="Checking the file…", max_entries=16)
def _read_for_staging(signature: tuple, path: str):
    return read_file_as_text(path)


with load_tab:
    repo_db = get_repository().db
    st.caption(f"Writes the file's rows into a staging table in the **{repo_db.profile.name}** "
               "database, under a batch ID. Everything downstream — transformation, curated, "
               "and the saved test cases — then runs on this data. Nothing is written until "
               "you press **Load**.")

    loadable = [t for t in by_name if not by_name[t]["sheet"]]
    guessed = [t for t in loadable if guess_staging_table(t)]
    c1, c2 = st.columns(2)
    src_name = c1.selectbox("File", loadable, format_func=_table_label, key="stg_file",
                            index=loadable.index(guessed[0]) if guessed else 0)
    targets = list(STAGING_TABLES)
    default_target = guess_staging_table(src_name)
    target = c2.selectbox("Staging table", targets, key=f"stg_target_{src_name}",
                          index=targets.index(default_target) if default_target else 0,
                          help="Guessed from the file name — change it if the guess is wrong.")

    c1, c2 = st.columns(2)
    load_batch = c1.text_input("Batch ID", value=batch_id(), key="stg_batch",
                               help="Defaults to the batch in the sidebar, which is the batch "
                                    "every test case runs against.")
    mode = c2.radio("Rows already staged for this batch", ["Replace them", "Keep them (append)"],
                    horizontal=True, key="stg_mode")

    path = os.path.join(folder, by_name[src_name]["file"])
    try:
        cols, rows = _read_for_staging(flatfile_signature(), path)
    except Exception as exc:
        st.error(f"Could not read {by_name[src_name]['file']}: {exc}")
        cols, rows = [], []
    check = check_for_staging(target, cols, rows)

    found = {c.strip().upper() for c in cols}
    mapping = pd.DataFrame([{"Column": c, "In the file": c in found}
                            for c in STAGING_TABLES[target][1]])
    left, right = st.columns([2, 3], gap="large")
    with left:
        st.markdown(f"**Columns {target} needs**")
        st.dataframe(mapping, use_container_width=True, hide_index=True,
                     column_config={"In the file": st.column_config.CheckboxColumn()})
    with right:
        st.markdown("**Checks**")
        if check.missing_columns:
            st.error(f"The file is missing {len(check.missing_columns)} column(s) {target} "
                     f"needs: {', '.join(check.missing_columns)}. Rename the file's headers "
                     "to match, or pick a different staging table.")
        for e in check.errors:
            st.error(e)
        for w in check.warnings:
            st.warning(w)
        if check.ok:
            st.success(f"{check.row_count:,} row(s) ready to load into {target}.")

    if check.ok:
        replace = mode == "Replace them"
        # A fresh key after each load un-ticks the box; Streamlit won't let us
        # reset a widget's own key once it has been drawn.
        confirm = st.checkbox(
            f"Write {check.row_count:,} row(s) to **{target}** in **{repo_db.profile.name}** for "
            f"batch **{load_batch}**" + (" — replacing that batch's current rows" if replace
                                        else " — keeping that batch's current rows"),
            key=f"stg_confirm_{st.session_state.get('stg_loads', 0)}")
        if st.button("⬆ Load into staging", type="primary", disabled=not confirm):
            try:
                with st.spinner(f"Loading {target}…"):
                    out = load_to_staging(repo_db, target, cols, rows, load_batch.strip(),
                                          source_file=by_name[src_name]["file"], replace=replace)
            except Exception as exc:
                msg = str(exc)
                if "does not exist" in msg.lower() or "no such table" in msg.lower():
                    msg += ("\n\nThe staging tables haven't been created yet: use **Create "
                            "tables** on the Demo Pipeline page, or run `python -m src.cli init`.")
                st.error(f"Nothing was loaded — the change was rolled back.\n\n{msg}")
            else:
                st.session_state["stg_last_load"] = out
                st.session_state["stg_loads"] = st.session_state.get("stg_loads", 0) + 1
                st.rerun()

    last = st.session_state.get("stg_last_load")
    if last:
        st.success(f"Loaded {last['inserted']:,} row(s) into {last['table']} for batch "
                   f"{last['batch_id']}"
                   + (f", replacing {last['deleted']:,} earlier row(s)" if last["deleted"] else "")
                   + f". {last['table']} now holds {last['staged_now']:,} row(s) for that batch.")
        staging_folder = None
        if project is not None:
            staging_folder = get_repository().find_folder_by_path(project["PROJECT_ID"],
                                                                  "/Staging")
        if staging_folder and st.button("▶ Run the /Staging test cases on this batch"):
            with st.spinner("Running…"):
                results = get_engine().run_folder(staging_folder["FOLDER_ID"],
                                                  batch_id=last["batch_id"])
            show_results(results)
        elif not staging_folder:
            st.caption("Next: run your staging test cases from the **Test Cases** page.")

# ------------------------------------------------------ test-case builder
with build_tab:
    if project is None:
        st.info("Create a project first — the test case is saved into one of its folders.")
        st.stop()

    repo = get_repository()
    folders = folder_options(repo, project["PROJECT_ID"])
    if not folders:
        st.info("This project has no folders yet. Create one on the home page.")
        st.stop()

    st.caption(f"Saves a test case into **{project['PROJECT_NAME']}** that compares a file "
               "(source) with a database table (target).")

    src_table = st.selectbox("Source file table", list(by_name), format_func=_table_label,
                             key="ff_build_table")
    cols = by_name[src_table]["columns"]
    targets = [c for c in available_connections() if c != "flatfile"] + ["flatfile"]

    with st.form("ff_build"):
        c1, c2 = st.columns(2)
        test_type = c1.radio("Check", ["SOURCE_TARGET_COMPARE", "ROW_COUNT_MATCH"],
                             format_func={"SOURCE_TARGET_COMPARE": "Row-by-row compare",
                                          "ROW_COUNT_MATCH": "Row count only"}.get,
                             horizontal=True)
        severity = c2.selectbox("Severity", SEVERITIES, index=1)

        c1, c2 = st.columns(2)
        tgt_conn = c1.selectbox("Target connection", targets)
        tgt_table = c2.text_input("Target table", value=f"STG_{src_table}",
                                  help="Schema-qualify it if it isn't in the default schema.")

        key_default = [c for c in cols if c.upper().endswith("_ID")][:1] or cols[:1]
        keys = st.multiselect("Key column(s)", cols, default=key_default,
                              help="The business key rows are matched on.")
        compare = st.multiselect("Columns to compare", [c for c in cols if c not in keys],
                                 help="Leave empty to compare every column the file has.")
        batch_filter = st.checkbox("Filter the target on `BATCH_ID = :batch_id`", value=True)

        c1, c2 = st.columns([3, 2])
        # A fixed key and no computed default: Streamlit rebuilds a widget whose default
        # changes, which would drop the typed name whenever the target table is edited.
        name = c1.text_input("Test case name", key="ff_build_name",
                             placeholder=f"{src_table} file vs <target table>",
                             help="Leave blank to use “<file table> file vs <target table>”.")
        folder_label = c2.selectbox("Save into folder", list(folders))

        submitted = st.form_submit_button("💾 Save test case", type="primary")

    if submitted:
        name = name.strip() or f"{src_table} file vs {tgt_table}"
        selected = keys + [c for c in (compare or cols) if c not in keys]
        where = "\nWHERE BATCH_ID = :batch_id" if batch_filter else ""
        if test_type == "ROW_COUNT_MATCH":
            source_sql = f"SELECT COUNT(*) AS ROW_COUNT FROM {src_table}"
            target_sql = f"SELECT COUNT(*) AS ROW_COUNT FROM {tgt_table}{where}"
        else:
            col_list = ",\n       ".join(selected)
            source_sql = f"SELECT {col_list}\nFROM {src_table}"
            target_sql = f"SELECT {col_list}\nFROM {tgt_table}{where}"

        if test_type == "SOURCE_TARGET_COMPARE" and not keys:
            st.error("Pick at least one key column.")
        else:
            try:
                created = repo.create_test_case(TestCase(
                    project_id=project["PROJECT_ID"], folder_id=folders[folder_label],
                    test_name=name, test_type=test_type, validation_scope="SOURCE_TO_TARGET",
                    entity=src_table.title(), layer="Staging", severity=severity,
                    description=f"Reconciles flat file {by_name[src_table]['file']} "
                                f"against {tgt_conn}:{tgt_table}.",
                    source_connection="flatfile", target_connection=tgt_conn,
                    source_sql=source_sql, target_sql=target_sql,
                    key_columns=",".join(keys) if test_type == "SOURCE_TARGET_COMPARE" else "",
                ))
            except (DuplicateNameError, ValueError) as exc:
                st.error(str(exc))
            else:
                path = repo.get_folder(created.folder_id)["FOLDER_PATH"]
                st.success(f"Saved as **{created.test_case_id}** in `{path}`. "
                           "Run it from the **Test Cases** page.")
                st.code(f"-- source (flatfile)\n{source_sql}\n\n-- target ({tgt_conn})\n"
                        f"{target_sql}", language="sql")
