"""The Dashboard's chart builders turn repository rows into valid chart specs."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "streamlit_app"))

import charts  # noqa: E402

LATEST = [
    {"FOLDER_PATH": "/Staging", "STATUS": "PASS", "RUN_TS": "2026-09-27 10:00:00"},
    {"FOLDER_PATH": "/Staging", "STATUS": "FAIL", "RUN_TS": "2026-09-27 10:01:00"},
    {"FOLDER_PATH": "/Historical", "STATUS": "ERROR", "RUN_TS": "2026-09-26 09:00:00"},
]
FAILURES = [{"FAILURE_TYPE": "VALUE_MISMATCH", "COLUMN_NAME": "PREMIUM_AMOUNT",
             "TEST_CASE_ID": "TC-00001"}] * 3 + [
            {"FAILURE_TYPE": "MISSING_IN_TARGET", "COLUMN_NAME": None,
             "TEST_CASE_ID": "TC-00002"}]
COUNTS = [{"layer": layer, "table": table, "total": n, "batch": n}
          for layer, table, n in [("Staging", "STG_CUSTOMER", 30), ("Staging", "STG_POLICY", 40),
                                  ("Staging", "STG_CLAIM", 50),
                                  ("Curated", "CUSTOMER_360", 30),
                                  ("Curated", "POLICY_MASTER", 40),
                                  ("Curated", "CLAIM_MASTER", 50)]]


def spec_data(chart):
    spec = chart.to_dict()
    return [r for ds in spec.get("datasets", {}).values() for r in ds]


def test_status_colours_follow_the_status_not_the_order():
    spec = charts.status_by_folder(LATEST).to_dict()
    scale = spec["encoding"]["color"]["scale"]
    assert dict(zip(scale["domain"], scale["range"])) == {
        "✓ Passed": "#0ca30c", "✕ Failed": "#d03b3b", "! Could not run": "#fab219"}


def test_every_chart_builds():
    for chart in (charts.status_by_folder(LATEST), charts.runs_over_time(LATEST),
                  charts.pass_rate_trend(LATEST),
                  charts.failures_by(FAILURES, "COLUMN_NAME", "Column"),
                  charts.records_by_layer(COUNTS)):
        assert chart.to_dict()["$schema"]


def test_pass_rate_is_per_day():
    rows = {r["Day"][:10]: r["rate"] for r in spec_data(charts.pass_rate_trend(LATEST))}
    assert rows == {"2026-09-27": 0.5, "2026-09-26": 0.0}


def test_failures_are_counted_largest_first_and_blanks_are_labelled():
    rows = spec_data(charts.failures_by(FAILURES, "COLUMN_NAME", "Column"))
    assert [(r["COLUMN_NAME"], r["Records"]) for r in rows] == [
        ("PREMIUM_AMOUNT", 3), ("(none)", 1)]


def test_each_entity_keeps_its_colour():
    spec = charts.records_by_layer(COUNTS).to_dict()
    colour = next(layer["encoding"]["color"] for layer in spec["layer"]
                  if "color" in layer.get("encoding", {}))
    assert colour["scale"] == {"domain": ["Customer", "Policy", "Claim"],
                               "range": ["#3987e5", "#d95926", "#199e70"]}


def test_tiles_escape_nothing_into_the_style_attribute():
    html = charts.stat_tiles_html([{"label": "Passed", "value": "17", "tone": "PASS"}])
    assert '"Segoe UI"' not in html and "✓" in html and "17" in html
