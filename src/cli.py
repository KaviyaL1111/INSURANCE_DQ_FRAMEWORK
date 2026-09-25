"""
Command-line interface.

    python -m src.cli doctor                 check the connection and print what it sees
    python -m src.cli init                   create repository + demo tables
    python -m src.cli seed-catalog           load the demo project, folders and test cases
    python -m src.cli load-data              run the ETL pipeline (injects 3 demo defects)
    python -m src.cli load-data --clean      run it with no defects
    python -m src.cli projects               list projects
    python -m src.cli folders                show the folder tree with test counts
    python -m src.cli tests                  list saved test cases
    python -m src.cli run --all              run every test case in the project
    python -m src.cli run --folder /Staging  run one folder
    python -m src.cli run --tests TC-00010   run specific test cases
    python -m src.cli history --start 2026-09-01 --end 2026-09-30
    python -m src.cli rerun --tests TC-00010,TC-00011
    python -m src.cli rerun --failed-since 2026-09-01
    python -m src.cli run-regression         run the /Regression folder
    python -m src.cli fix                    correct the demo defects
    python -m src.cli dashboard              pass/fail summary
    python -m src.cli demo                   the whole walkthrough, end to end
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta

from tabulate import tabulate

from src import etl_loader
from src.config import (DEFAULT_BATCH_ID, DEFAULT_CONNECTION, DEMO_PROJECT, TIMEZONE_LABEL,
                        available_connections, today_ist)
from src.connectors import get_connector
from src.dq_engine import DQEngine
from src.history import HistoryStore
from src.regression_engine import RegressionEngine
from src.repository import Repository
from src.seed import seed_catalog

OK, BAD, WARN = "✓", "✗", "!"


def _table(rows, headers="keys"):
    if not rows:
        return "  (nothing to show)"
    return tabulate(rows, headers=headers, tablefmt="simple")


def _resolve_project(repo: Repository, name: str | None) -> dict:
    if name:
        project = repo.find_project_by_name(name)
        if project is None:
            raise SystemExit(f"{BAD} No project named '{name}'. Run: python -m src.cli projects")
        return project
    projects = repo.list_projects()
    if not projects:
        raise SystemExit(f"{BAD} No projects yet. Run: python -m src.cli seed-catalog")
    demo = next((p for p in projects if p["PROJECT_NAME"] == DEMO_PROJECT), None)
    return demo or projects[0]


def _print_results(results):
    rows = [[r.test_case_id, r.test_name[:48], r.status, r.failed_record_count,
             f"{r.duration_ms} ms"] for r in results]
    print(_table(rows, headers=["Test", "Name", "Status", "Failures", "Time"]))
    if results:
        passed = sum(1 for r in results if r.status == "PASS")
        print(f"\n  {passed}/{len(results)} passed ({passed / len(results) * 100:.1f}%)")
    errors = [r for r in results if r.status == "ERROR"]
    for r in errors:
        print(f"  {BAD} {r.test_case_id} errored: {r.error_message}")


def _print_failures(history: HistoryStore, results, limit: int = 20):
    shown = 0
    for r in results:
        if r.status != "FAIL":
            continue
        rows = history.get_failures(execution_id=r.execution_id, limit=limit)
        if not rows:
            continue
        print(f"\n  {r.test_case_id} — {r.test_name}  ({r.failed_record_count} failed record(s))")
        print(_table([{k: v for k, v in row.items()
                       if k in ("BUSINESS_KEY", "COLUMN_NAME", "EXPECTED_VALUE",
                                "ACTUAL_VALUE", "FAILURE_TYPE")} for row in rows[:limit]]))
        shown += 1
        if shown >= 5:
            print("\n  (more failures in the dashboard)")
            break


# --------------------------------------------------------------- commands
def cmd_doctor(args):
    print(f"Default connection : {DEFAULT_CONNECTION}")
    print(f"Configured profiles: {', '.join(available_connections()) or '(none — check your .env)'}")
    try:
        conn = get_connector(args.connection)
    except Exception as exc:
        raise SystemExit(f"{BAD} Could not build the connection profile:\n   {exc}")
    try:
        if conn.kind == "flatfile":
            tables = conn.describe()
            print(f"\n{OK} Read {len(tables)} table(s) from {conn.profile.options['path']}")
            print(_table([[t["table"], t["file"], t["rows"], len(t["columns"])] for t in tables],
                         headers=["Table", "File", "Rows", "Columns"]))
            for err in conn.load_errors:
                print(f"  {WARN} {err['file']}: {err['error']}")
            return
        if conn.kind == "snowflake":
            res = conn.query("""SELECT CURRENT_ACCOUNT(), CURRENT_USER(), CURRENT_ROLE(),
                                       CURRENT_WAREHOUSE(), CURRENT_DATABASE(), CURRENT_SCHEMA(),
                                       CURRENT_VERSION()""")
            labels = ["Account", "User", "Role", "Warehouse", "Database", "Schema", "Version"]
        else:
            res = conn.query("SELECT @@SERVERNAME, SUSER_NAME(), DB_NAME(), @@VERSION")
            labels = ["Server", "Login", "Database", "Version"]
        print(f"\n{OK} Connected.")
        for label, value in zip(labels, res.rows[0]):
            print(f"  {label:<10} {value}")
        for warn_if_none in ("Warehouse", "Database", "Schema"):
            if warn_if_none in labels and res.rows[0][labels.index(warn_if_none)] is None:
                print(f"  {WARN} {warn_if_none} is not set — check SNOWFLAKE_{warn_if_none.upper()} in .env")

        tables = conn.query(
            """SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES
               WHERE TABLE_SCHEMA = CURRENT_SCHEMA() AND TABLE_NAME LIKE 'DQ$_%' ESCAPE '$'"""
        ).rows[0][0] if conn.kind == "snowflake" else None
        if tables is not None:
            print(f"\n  Repository tables present: {tables}"
                  + ("   (run `python -m src.cli init` to create them)" if not tables else ""))
    finally:
        conn.close()


def cmd_init(args):
    etl_loader.init_schema()
    print(f"{OK} Repository control tables and insurance demo tables created.")
    print("  Next: python -m src.cli seed-catalog")


def cmd_seed(args):
    summary = seed_catalog(update_existing=not args.no_update)
    print(f"{OK} Seeded project '{summary['project']}'")
    print(f"  folders {summary['folders']} · created {summary['created']} · "
          f"updated {summary['updated']} · regression copies {summary['regression_copies']}")


def cmd_load_data(args):
    result = etl_loader.run_full_pipeline(batch_id=args.batch_id,
                                          inject_mismatches=not args.clean)
    print(f"{OK} Pipeline loaded for batch {result['batch_id']}"
          + (" (clean)" if args.clean else " — 3 demo defects injected"))
    print(_table([result["row_counts"]]))


def cmd_corrupt(args):
    etl_loader.introduce_mismatches(args.batch_id)
    print(f"{OK} Injected 3 defects: P1005 premium, CL1007 status, CL1010 orphan.")


def cmd_fix(args):
    etl_loader.fix_mismatches(args.batch_id)
    print(f"{OK} Defects corrected from the transformation layer.")


def cmd_projects(args):
    with Repository() as repo:
        print(_table([{k: v for k, v in p.items()
                       if k in ("PROJECT_NAME", "DESCRIPTION", "CREATED_BY", "CREATED_TS")}
                      for p in repo.list_projects()]))


def cmd_folders(args):
    with Repository() as repo:
        project = _resolve_project(repo, args.project)
        print(f"Project: {project['PROJECT_NAME']}\n")
        for f in repo.folder_tree(project["PROJECT_ID"]):
            depth = f["FOLDER_PATH"].count("/") - 1
            marker = "[R]" if f["FOLDER_TYPE"] == "REGRESSION" else "   "
            print(f"  {marker} {'  ' * depth}{f['FOLDER_NAME']}  ({f['TEST_COUNT']} tests)")


def cmd_tests(args):
    with Repository() as repo:
        project = _resolve_project(repo, args.project)
        folder_id = None
        if args.folder:
            folder = repo.find_folder_by_path(project["PROJECT_ID"], args.folder)
            if folder is None:
                raise SystemExit(f"{BAD} No folder '{args.folder}' in {project['PROJECT_NAME']}")
            folder_id = folder["FOLDER_ID"]
        rows = repo.list_test_cases(folder_id=folder_id, project_id=project["PROJECT_ID"])
        print(_table([{"ID": r["TEST_CASE_ID"], "Folder": r["FOLDER_PATH"],
                       "Name": r["TEST_NAME"][:44], "Type": r["TEST_TYPE"],
                       "Severity": r["SEVERITY"], "v": r["VERSION_NO"]} for r in rows]))
        print(f"\n  {len(rows)} test case(s)")


def cmd_run(args):
    params = _parse_params(args.param)
    with Repository() as repo:
        project = _resolve_project(repo, args.project)
        engine = DQEngine(repository=repo)
        try:
            if args.tests:
                ids = [t.strip() for t in args.tests.split(",") if t.strip()]
                cases = repo.get_test_cases(ids)
            elif args.folder:
                folder = repo.find_folder_by_path(project["PROJECT_ID"], args.folder)
                if folder is None:
                    raise SystemExit(f"{BAD} No folder '{args.folder}'")
                rows = repo.list_test_cases(folder_id=folder["FOLDER_ID"])
                cases = repo.get_test_cases([r["TEST_CASE_ID"] for r in rows])
            else:
                rows = repo.list_test_cases(project_id=project["PROJECT_ID"])
                cases = repo.get_test_cases([r["TEST_CASE_ID"] for r in rows])
            if not cases:
                raise SystemExit(f"{BAD} No test cases matched. Run: python -m src.cli tests")
            results = engine.run_test_cases(cases, params=params, batch_id=args.batch_id,
                                            run_mode="FOLDER" if args.folder else "ADHOC")
            _print_results(results)
            if args.show_failures:
                _print_failures(engine.history, results)
        finally:
            engine.close()


def cmd_history(args):
    with Repository() as repo:
        project = _resolve_project(repo, args.project) if not args.all_projects else None
        rows = HistoryStore(repo.db).get_execution_history(
            args.start, args.end,
            project_id=project["PROJECT_ID"] if project else "",
            status=args.status or "", latest_per_test=args.latest,
        )
        print(_table([{"Test": r["TEST_CASE_ID"], "Name": (r["TEST_NAME"] or "")[:36],
                       f"Run ({TIMEZONE_LABEL})": r["RUN_TS"], "Status": r["STATUS"],
                       "Failures": r["FAILED_RECORD_COUNT"], "Mode": r["RUN_MODE"],
                       "v": r["TEST_VERSION_NO"], "By": r["EXECUTED_BY"]} for r in rows]))
        print(f"\n  {len(rows)} execution(s) between {args.start} and {args.end}")


def cmd_rerun(args):
    params = _parse_params(args.param)
    with RegressionEngine() as eng:
        project = _resolve_project(eng.repo, args.project)
        if args.failed_since:
            end = args.failed_until or today_ist().isoformat()
            ids = eng.failed_test_case_ids(args.failed_since, end,
                                           project_id=project["PROJECT_ID"])
            if not ids:
                print(f"{OK} Nothing failing between {args.failed_since} and {end}.")
                return
            print(f"Selected {len(ids)} previously-failing test case(s): {', '.join(ids)}")
            outcome = eng.rerun_selected(ids, params=params, batch_id=args.batch_id,
                                         project_id=project["PROJECT_ID"],
                                         history_start=args.failed_since, history_end=end)
        else:
            ids = [t.strip() for t in (args.tests or "").split(",") if t.strip()]
            if not ids:
                raise SystemExit(f"{BAD} Pass --tests TC-00001,TC-00002 or --failed-since YYYY-MM-DD")
            outcome = eng.rerun_selected(ids, params=params, batch_id=args.batch_id,
                                         project_id=project["PROJECT_ID"],
                                         triggered_from="CLI")
        _print_results(outcome.results)
        for tcid, why in outcome.skipped:
            print(f"  {WARN} skipped {tcid}: {why}")
        print(f"\n  {outcome.summary()}")
        if args.show_failures:
            _print_failures(eng.history, outcome.results)


def cmd_run_regression(args):
    params = _parse_params(args.param)
    with RegressionEngine() as eng:
        project = _resolve_project(eng.repo, args.project)
        outcome = eng.run_regression_folder(project["PROJECT_ID"], folder_path=args.folder,
                                            params=params, batch_id=args.batch_id)
        _print_results(outcome.results)
        print(f"\n  {outcome.summary()}")
        if args.show_failures:
            _print_failures(eng.history, outcome.results)
        if outcome.all_passed:
            print(f"  {OK} Regression complete — all test cases pass.")


def cmd_dashboard(args):
    with Repository() as repo:
        project = _resolve_project(repo, args.project)
        history = HistoryStore(repo.db)
        s = history.dashboard_summary(project["PROJECT_ID"])
        print(f"Project: {project['PROJECT_NAME']}   (latest run of each test case)\n")
        print(_table([{"Total": s["total"], "Passed": s["passed"], "Failed": s["failed"],
                       "Errored": s["errored"], "Pass %": s["pass_pct"] if s["pass_pct"] is not None else "-",
                       "Failed records": s["failed_records"]}]))
        rows = history.latest_failures_for_project(project["PROJECT_ID"], limit=30)
        if rows:
            print("\nFailed records (most recent run of each test):\n")
            print(_table([{k: (str(v)[:34] if v is not None else "") for k, v in r.items()
                           if k in ("TEST_CASE_ID", "BUSINESS_KEY", "COLUMN_NAME",
                                    "EXPECTED_VALUE", "ACTUAL_VALUE", "FAILURE_TYPE")}
                          for r in rows]))
        else:
            print(f"\n{OK} No failing records in the latest run of any test case.")


def cmd_demo(args):
    """The full brief, end to end, in one command."""
    steps = []

    def step(n, label):
        print(f"\n{'=' * 66}\n  STEP {n}: {label}\n{'=' * 66}")

    step(1, "Create the repository and the insurance tables")
    etl_loader.init_schema()
    print(f"{OK} done")

    step(2, "Seed the project, folder structure and saved test cases")
    summary = seed_catalog()
    print(f"{OK} {summary['created'] + summary['updated']} test cases across "
          f"{summary['folders']} folders in '{summary['project']}'")

    step(3, "Load source -> target, with 3 intentional mismatches")
    result = etl_loader.run_full_pipeline(batch_id=args.batch_id, inject_mismatches=True)
    print(_table([result["row_counts"]]))

    with RegressionEngine() as eng:
        project = _resolve_project(eng.repo, DEMO_PROJECT)
        pid = project["PROJECT_ID"]

        step(4, "Run the full validation suite — mismatches should be caught")
        first = eng.run_full_suite(pid, batch_id=args.batch_id)
        _print_results(first.results)
        steps.append(("initial run", first))
        print("\n  Failed records with mismatch detail:")
        _print_failures(eng.history, first.results)

        step(5, "Correct the data defects")
        etl_loader.fix_mismatches(args.batch_id)
        print(f"{OK} corrected")

        step(6, "Rerun the saved scripts from the /Regression folder")
        regression = eng.run_regression_folder(pid, "/Regression", batch_id=args.batch_id)
        _print_results(regression.results)
        steps.append(("regression rerun", regression))

        step(7, "Rerun everything that failed, selected from execution history")
        today = today_ist()
        outcome = eng.rerun_failed_in_window(today - timedelta(days=1), today,
                                             project_id=pid, batch_id=args.batch_id)
        _print_results(outcome.results)
        steps.append(("history rerun", outcome))

        step(8, "Dashboard")
        s = eng.history.dashboard_summary(pid)
        print(_table([s]))

    print(f"\n{'=' * 66}")
    for label, o in steps:
        print(f"  {label:<20} {o.summary()}")
    final = steps[-1][1] if steps else None
    if final is not None and final.total and final.all_passed:
        print(f"\n  {OK} Demo complete: defects were caught, corrected, and the "
              f"rerun now passes.")
    print("=" * 66)


def _parse_params(pairs: list[str] | None) -> dict:
    out = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SystemExit(f"{BAD} --param expects name=value, got '{pair}'")
        k, v = pair.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def main():
    p = argparse.ArgumentParser(
        prog="python -m src.cli",
        description="Insurance data quality & regression automation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--batch-id", default=DEFAULT_BATCH_ID, dest="batch_id")
    p.add_argument("--project", help="Project name (defaults to the demo project)")
    p.add_argument("--connection", help="Connection profile to use")
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, fn, **kw):
        sp = sub.add_parser(name, **kw)
        sp.set_defaults(func=fn)
        return sp

    add("doctor", cmd_doctor, help="Check the database connection")
    add("init", cmd_init, help="Create repository + demo tables")
    s = add("seed-catalog", cmd_seed, help="Load the demo project and test cases")
    s.add_argument("--no-update", action="store_true", help="Skip test cases that already exist")

    s = add("load-data", cmd_load_data, help="Run the ETL pipeline")
    s.add_argument("--clean", action="store_true", help="Do not inject the demo defects")

    add("corrupt", cmd_corrupt, help="Inject the 3 demo defects")
    add("fix", cmd_fix, help="Correct the 3 demo defects")
    add("projects", cmd_projects, help="List projects")

    s = add("folders", cmd_folders, help="Show the folder tree")
    s = add("tests", cmd_tests, help="List saved test cases")
    s.add_argument("--folder", help="Only this folder path, e.g. /Staging")

    s = add("run", cmd_run, help="Execute saved test cases")
    s.add_argument("--tests", help="Comma-separated test case ids")
    s.add_argument("--folder", help="Run one folder, e.g. /Source-to-Target")
    s.add_argument("--all", action="store_true", help="Run every test in the project")
    s.add_argument("--param", action="append", help="name=value (repeatable)")
    s.add_argument("--show-failures", action="store_true", help="Print mismatch detail")

    s = add("history", cmd_history, help="Execution history for a date range")
    s.add_argument("--start", required=True, help="YYYY-MM-DD")
    s.add_argument("--end", required=True, help="YYYY-MM-DD")
    s.add_argument("--status", choices=["PASS", "FAIL", "ERROR"])
    s.add_argument("--latest", action="store_true", help="Latest run per test case only")
    s.add_argument("--all-projects", action="store_true")

    s = add("rerun", cmd_rerun, help="Rerun selected test cases at their latest saved config")
    s.add_argument("--tests", help="Comma-separated test case ids")
    s.add_argument("--failed-since", help="Select everything that failed since YYYY-MM-DD")
    s.add_argument("--failed-until", help="End of that window (default: today)")
    s.add_argument("--param", action="append")
    s.add_argument("--show-failures", action="store_true")

    s = add("run-regression", cmd_run_regression, help="Run the /Regression folder")
    s.add_argument("--folder", default="/Regression")
    s.add_argument("--param", action="append")
    s.add_argument("--show-failures", action="store_true")

    add("dashboard", cmd_dashboard, help="Pass/fail summary and failed records")
    add("demo", cmd_demo, help="Run the entire walkthrough end to end")

    args = p.parse_args()
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 1
    except EnvironmentError as exc:
        print(f"\n{BAD} {exc}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
