"""
Integration tests against a real Snowflake account.

Skipped automatically unless SNOWFLAKE_ACCOUNT is configured, so the suite
stays green on a machine with no credentials. Run them with:

    pytest tests/test_integration_snowflake.py -v

They exercise the real pipeline end to end and leave the demo batch loaded.
"""
import os
from datetime import date

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("SNOWFLAKE_ACCOUNT"),
    reason="Snowflake is not configured in this environment",
)

from src import etl_loader                                    # noqa: E402
from src.config import DEFAULT_BATCH_ID, DEMO_PROJECT         # noqa: E402
from src.regression_engine import RegressionEngine            # noqa: E402
from src.repository import Repository                         # noqa: E402
from src.seed import seed_catalog                             # noqa: E402

BATCH = os.getenv("DQ_TEST_BATCH_ID", DEFAULT_BATCH_ID)


@pytest.fixture(scope="module")
def project():
    etl_loader.init_schema()
    seed_catalog()
    with Repository() as repo:
        yield repo.find_project_by_name(DEMO_PROJECT)


@pytest.fixture(scope="module")
def reg():
    engine = RegressionEngine(executed_by="pytest")
    yield engine
    engine.close()


def test_clean_load_passes_every_test_case(project, reg):
    """The demo dataset is built so that a clean load has no defects at all."""
    etl_loader.run_full_pipeline(batch_id=BATCH, inject_mismatches=False)
    outcome = reg.run_full_suite(project["PROJECT_ID"], batch_id=BATCH)
    failing = [(r.test_name, r.status, r.error_message) for r in outcome.results
               if r.status != "PASS"]
    assert not failing, f"clean load should be green, got: {failing}"


def test_injected_defects_are_caught_with_detail(project, reg):
    etl_loader.introduce_mismatches(BATCH)
    outcome = reg.run_full_suite(project["PROJECT_ID"], batch_id=BATCH)
    assert outcome.failed >= 3, outcome.summary()

    detail = {}
    for r in outcome.results:
        for f in r.failures:
            detail[(f.business_key, f.column_name)] = (f.expected_value, f.actual_value)

    assert detail.get(("P1005", "PREMIUM_AMOUNT")) == ("12000", "10000")
    assert detail.get(("CL1007", "CLAIM_STATUS")) == ("Approved", "Pending")
    assert ("CL1010", "POLICY_ID") in detail


def test_correction_then_regression_folder_rerun_passes(project, reg):
    etl_loader.fix_mismatches(BATCH)
    outcome = reg.run_regression_folder(project["PROJECT_ID"], "/Regression", batch_id=BATCH)
    assert outcome.total > 0
    assert outcome.all_passed, [(r.test_name, r.status) for r in outcome.results]


def test_history_selection_and_rerun(project, reg):
    today = date.today()
    history = reg.get_execution_history(today, today, project_id=project["PROJECT_ID"])
    assert history, "the runs above should be in history"

    outcome = reg.rerun_failed_in_window(today, today, project_id=project["PROJECT_ID"],
                                         batch_id=BATCH)
    assert outcome.all_passed or outcome.total == 0


def test_full_suite_is_green_after_correction(project, reg):
    outcome = reg.run_full_suite(project["PROJECT_ID"], batch_id=BATCH)
    assert outcome.all_passed, [(r.test_name, r.status, r.error_message)
                                for r in outcome.results if r.status != "PASS"]
    summary = reg.history.dashboard_summary(project["PROJECT_ID"])
    assert summary["failed"] == 0 and summary["pass_pct"] == 100.0
