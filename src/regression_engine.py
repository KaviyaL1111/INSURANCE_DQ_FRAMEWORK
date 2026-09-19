"""
Regression engine.

Implements the rerun workflow the brief specifies:

    1. the user opens Execution History
    2. the user enters a start date and an end date
    3. the application loads all matching test executions
    4. the application displays them with selection controls
    5. the user selects the required test cases
    6. the user chooses "Execute Selected Tests"
    7. the application loads the LATEST SAVED CONFIGURATION of each
       selected test case and reruns it

Step 7 is the part that matters: the rerun does not replay the SQL that was
executed historically, it reloads each test case from the repository. So a
test case corrected after it failed is rerun in its corrected form, which is
exactly what a regression cycle needs.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.config import DEFAULT_BATCH_ID, DEFAULT_USER
from src.dq_engine import DQEngine
from src.history import ExecutionResult, HistoryStore, new_run_id
from src.repository import Repository


@dataclass
class RegressionOutcome:
    run_id: str = ""
    regression_id: str = ""
    results: list[ExecutionResult] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (test_case_id, why)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.status == "PASS")

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == "FAIL")

    @property
    def errored(self) -> int:
        return sum(1 for r in self.results if r.status == "ERROR")

    @property
    def all_passed(self) -> bool:
        return bool(self.results) and self.passed == self.total

    @property
    def pass_pct(self) -> float | None:
        return round(self.passed / self.total * 100, 1) if self.total else None

    def summary(self) -> str:
        if not self.results:
            return "No test cases were executed."
        parts = [f"{self.passed}/{self.total} passed ({self.pass_pct}%)"]
        if self.failed:
            parts.append(f"{self.failed} failed")
        if self.errored:
            parts.append(f"{self.errored} errored")
        if self.skipped:
            parts.append(f"{len(self.skipped)} skipped")
        return " · ".join(parts)


class RegressionEngine:
    def __init__(self, repository: Repository | None = None, executed_by: str = ""):
        self.repo = repository or Repository()
        self._own_repo = repository is None
        self.history = HistoryStore(self.repo.db)
        self.executed_by = executed_by or DEFAULT_USER
        self.engine = DQEngine(repository=self.repo, executed_by=self.executed_by)

    def close(self):
        self.engine.close()
        if self._own_repo:
            self.repo.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # ------------------------------------------------- steps 1-4: the history
    def get_execution_history(self, start, end, project_id: str = "", folder_path: str = "",
                              status: str = "", latest_per_test: bool = False) -> list[dict]:
        return self.history.get_execution_history(
            start, end, project_id=project_id, folder_path=folder_path,
            status=status, latest_per_test=latest_per_test,
        )

    def failed_test_case_ids(self, start, end, project_id: str = "") -> list[str]:
        """Distinct test cases whose most recent run in the window failed —
        the natural default selection on the history screen."""
        rows = self.history.get_execution_history(
            start, end, project_id=project_id, latest_per_test=True)
        return [r["TEST_CASE_ID"] for r in rows if r["STATUS"] in ("FAIL", "ERROR")]

    # ----------------------------------------------- steps 5-7: the actual rerun
    def rerun_selected(self, test_case_ids: list[str], params: dict | None = None,
                       batch_id: str = DEFAULT_BATCH_ID, project_id: str = "",
                       triggered_from: str = "EXECUTION_HISTORY",
                       history_start=None, history_end=None,
                       on_progress=None) -> RegressionOutcome:
        """
        Rerun the selected test cases at their latest saved configuration.

        Duplicates in the selection are collapsed — the history screen lists
        one row per execution, so picking three runs of the same test case
        should still execute it once.
        """
        ordered_unique, seen = [], set()
        for tcid in test_case_ids:
            if tcid and tcid not in seen:
                seen.add(tcid)
                ordered_unique.append(tcid)

        outcome = RegressionOutcome(run_id=new_run_id())
        cases = []
        for tcid in ordered_unique:
            try:
                tc = self.repo.get_test_case(tcid)          # <- latest saved configuration
            except Exception as exc:
                outcome.skipped.append((tcid, f"could not be loaded: {exc}"))
                continue
            if not tc.is_active:
                outcome.skipped.append((tcid, "test case is inactive (deleted)"))
                continue
            cases.append(tc)
            project_id = project_id or tc.project_id

        outcome.regression_id = self.history.start_regression_run(
            run_id=outcome.run_id, project_id=project_id,
            triggered_from=triggered_from, selected_ids=[c.test_case_id for c in cases],
            history_start=history_start, history_end=history_end,
            executed_by=self.executed_by,
        )
        outcome.results = self.engine.run_test_cases(
            cases, params=params, batch_id=batch_id, run_id=outcome.run_id,
            run_mode="REGRESSION_RERUN", on_progress=on_progress,
        )
        self.history.complete_regression_run(outcome.regression_id, outcome.results)
        return outcome

    def rerun_failed_in_window(self, start, end, project_id: str = "",
                               params: dict | None = None,
                               batch_id: str = DEFAULT_BATCH_ID) -> RegressionOutcome:
        """Convenience: select every test that last failed in the window, rerun it."""
        ids = self.failed_test_case_ids(start, end, project_id=project_id)
        return self.rerun_selected(ids, params=params, batch_id=batch_id, project_id=project_id,
                                   triggered_from="EXECUTION_HISTORY",
                                   history_start=start, history_end=end)

    # ------------------------------------------------------- folder-driven runs
    def run_regression_folder(self, project_id: str, folder_path: str = "/Regression",
                              params: dict | None = None, batch_id: str = DEFAULT_BATCH_ID,
                              on_progress=None) -> RegressionOutcome:
        """
        Run every saved test case in a folder — the "rerun the saved
        regression script from the Regression folder" step of the demo.
        """
        folder = self.repo.find_folder_by_path(project_id, folder_path)
        if folder is None:
            raise LookupError(
                f"Project has no folder '{folder_path}'. "
                f"Create it on the Projects & Folders screen, or run `python -m src.cli seed-catalog`."
            )
        rows = self.repo.list_test_cases(folder_id=folder["FOLDER_ID"])
        outcome = RegressionOutcome(run_id=new_run_id())
        cases = self.repo.get_test_cases([r["TEST_CASE_ID"] for r in rows])
        outcome.regression_id = self.history.start_regression_run(
            run_id=outcome.run_id, project_id=project_id, folder_path=folder_path,
            triggered_from="REGRESSION_FOLDER",
            selected_ids=[c.test_case_id for c in cases], executed_by=self.executed_by,
        )
        outcome.results = self.engine.run_test_cases(
            cases, params=params, batch_id=batch_id, run_id=outcome.run_id,
            run_mode="REGRESSION_RERUN", on_progress=on_progress,
        )
        self.history.complete_regression_run(outcome.regression_id, outcome.results)
        return outcome

    def run_full_suite(self, project_id: str, params: dict | None = None,
                       batch_id: str = DEFAULT_BATCH_ID, on_progress=None) -> RegressionOutcome:
        """
        Every regression-eligible test case in the project, in folder order.

        Folder order comes from FOLDER_PATH, so the suite naturally runs
        Staging before Transformation before Source-to-Target — no separate
        hardcoded ordering list to drift out of date.
        """
        rows = self.repo.list_test_cases(project_id=project_id, regression_only=True)
        cases = self.repo.get_test_cases([r["TEST_CASE_ID"] for r in rows])
        outcome = RegressionOutcome(run_id=new_run_id())
        outcome.regression_id = self.history.start_regression_run(
            run_id=outcome.run_id, project_id=project_id, folder_path="(all)",
            triggered_from="FULL_SUITE", selected_ids=[c.test_case_id for c in cases],
            executed_by=self.executed_by,
        )
        outcome.results = self.engine.run_test_cases(
            cases, params=params, batch_id=batch_id, run_id=outcome.run_id,
            run_mode="FULL_SUITE", on_progress=on_progress,
        )
        self.history.complete_regression_run(outcome.regression_id, outcome.results)
        return outcome


# Backwards-compatible module-level helpers -----------------------------------
def get_execution_history(start, end, **kw) -> list[dict]:
    with RegressionEngine() as eng:
        return eng.get_execution_history(start, end, **kw)


def rerun_selected(test_case_ids: list[str], **kw) -> RegressionOutcome:
    with RegressionEngine() as eng:
        return eng.rerun_selected(test_case_ids, **kw)
