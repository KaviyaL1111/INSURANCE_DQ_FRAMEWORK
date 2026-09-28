"""
Charts for the Dashboard.

Colours are assigned by the job they do, never cycled:

  * STATUS (pass / fail / could not run) uses the fixed status palette and
    is always paired with a label and icon, so it never carries meaning alone.
  * ENTITY (customer / policy / claim) uses the first three categorical slots,
    in a fixed order, so an entity keeps its colour whatever is filtered.
  * Single-measure bars and the trend line use one hue.

The server can't reliably tell whether the app is showing its light or dark
theme, so charts are drawn with theme="streamlit" (axes, grid and background
follow the app) and the tiles inherit the page's text colour. The mark
colours below were run through the dataviz palette validator against both
the light and the dark surface and pass every check on both.
"""
from __future__ import annotations

import altair as alt
import pandas as pd

STATUS_ORDER = ["PASS", "FAIL", "ERROR"]
STATUS_LABEL = {"PASS": "✓ Passed", "FAIL": "✕ Failed", "ERROR": "! Could not run"}
STATUS_COLOR = {"PASS": "#0ca30c", "FAIL": "#d03b3b", "ERROR": "#fab219"}

ENTITY_ORDER = ["Customer", "Policy", "Claim"]
ENTITY_COLOR = ["#3987e5", "#d95926", "#199e70"]   # categorical slots 1-3, fixed order
LAYER_ORDER = ["Staging", "Transformation", "Curated"]
ACCENT = "#3987e5"
MUTED = "#898781"                                   # value labels: legible on both themes

FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'
_HTML_FONT = FONT.replace('"', "'")                 # safe inside a style="..." attribute


def _style(chart, height: int):
    """Legend on top with square swatches; everything else comes from the app theme."""
    return (chart.properties(height=height, padding={"left": 5, "top": 5, "right": 18,
                                                     "bottom": 5})
            .configure_view(stroke=None)
            .configure_axis(labelFontSize=11, titleFontSize=11, titleFontWeight="normal")
            .configure_legend(orient="top", symbolType="square", symbolSize=120,
                              labelFontSize=12, title=None))


def _status_scale():
    return alt.Scale(domain=[STATUS_LABEL[s] for s in STATUS_ORDER],
                     range=[STATUS_COLOR[s] for s in STATUS_ORDER])


def _with_status_labels(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["Result"] = df["STATUS"].map(STATUS_LABEL).fillna(df["STATUS"])
    df["order"] = df["STATUS"].map({s: i for i, s in enumerate(STATUS_ORDER)})
    return df


def _by_day(runs: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(runs)
    df["Day"] = pd.to_datetime(df["RUN_TS"]).dt.normalize()
    return df


_DAY_AXIS = alt.Axis(format="%d %b", labelAngle=0, grid=False, tickCount=8)


# ---------------------------------------------------------------- charts
def status_by_folder(latest: list[dict]):
    """Latest result of every test case, stacked per folder (part-to-whole, horizontal)."""
    df = pd.DataFrame(latest).groupby(["FOLDER_PATH", "STATUS"]).size().reset_index(name="Tests")
    df = _with_status_labels(df)
    folders = sorted(df["FOLDER_PATH"].unique())
    chart = alt.Chart(df).mark_bar(height={"band": 0.6}).encode(
        y=alt.Y("FOLDER_PATH:N", title=None, sort=folders,
                axis=alt.Axis(grid=False, labelOverlap=False)),
        x=alt.X("Tests:Q", title="Test cases (latest run)", stack="zero",
                axis=alt.Axis(tickMinStep=1, format="d")),
        color=alt.Color("Result:N", scale=_status_scale()),
        order=alt.Order("order:Q"),
        tooltip=[alt.Tooltip("FOLDER_PATH:N", title="Folder"),
                 alt.Tooltip("Result:N"), alt.Tooltip("Tests:Q")],
    )
    return _style(chart, height=max(180, 40 * len(folders)))


def runs_over_time(runs: list[dict]):
    """Executions per day, stacked by result (every run, not only the latest)."""
    df = _by_day(runs).groupby(["Day", "STATUS"]).size().reset_index(name="Executions")
    df = _with_status_labels(df)
    chart = alt.Chart(df).mark_bar().encode(
        x=alt.X("yearmonthdate(Day):T", title=None, axis=_DAY_AXIS),
        y=alt.Y("Executions:Q", title="Executions", stack="zero",
                axis=alt.Axis(tickMinStep=1, format="d")),
        color=alt.Color("Result:N", scale=_status_scale()),
        order=alt.Order("order:Q"),
        tooltip=[alt.Tooltip("yearmonthdate(Day):T", title="Day", format="%d %b %Y"),
                 alt.Tooltip("Result:N"), alt.Tooltip("Executions:Q")],
    )
    return _style(chart, height=240)


def pass_rate_trend(runs: list[dict]):
    """Share of executions that passed, per day: one series, one hue, no legend."""
    df = _by_day(runs)
    df = (df.assign(passed=df["STATUS"].eq("PASS"))
          .groupby("Day").agg(rate=("passed", "mean"), runs=("passed", "size")).reset_index())
    base = alt.Chart(df).encode(
        x=alt.X("yearmonthdate(Day):T", title=None, axis=_DAY_AXIS),
        y=alt.Y("rate:Q", title="Passed", scale=alt.Scale(domain=[0, 1]),
                axis=alt.Axis(format="%", tickCount=5)),
        tooltip=[alt.Tooltip("yearmonthdate(Day):T", title="Day", format="%d %b %Y"),
                 alt.Tooltip("rate:Q", title="Pass rate", format=".0%"),
                 alt.Tooltip("runs:Q", title="Executions")],
    )
    chart = alt.layer(base.mark_area(color=ACCENT, opacity=0.15),
                      base.mark_line(color=ACCENT, strokeWidth=2),
                      base.mark_circle(color=ACCENT, size=70, opacity=1))
    return _style(chart, height=240)


def failures_by(failures: list[dict], field: str, title: str, top: int = 8):
    """Failed records counted by one field, largest first: magnitude, so one hue."""
    df = pd.DataFrame(failures)
    df[field] = df[field].fillna("(none)").astype(str)
    df = (df.groupby(field).size().reset_index(name="Records")
          .sort_values("Records", ascending=False).head(top))
    bars = alt.Chart(df).encode(
        y=alt.Y(f"{field}:N", title=None, sort="-x", axis=alt.Axis(grid=False, labelLimit=180)),
        x=alt.X("Records:Q", title="Failed records", axis=alt.Axis(tickMinStep=1, format="d")),
        tooltip=[alt.Tooltip(f"{field}:N", title=title), alt.Tooltip("Records:Q")],
    )
    chart = (bars.mark_bar(color=ACCENT, cornerRadiusEnd=4, height={"band": 0.6})
             + bars.mark_text(align="left", dx=4, color=MUTED).encode(text="Records:Q"))
    return _style(chart, height=max(140, 30 * len(df)))


def records_by_layer(counts: list[dict]):
    """Rows per pipeline layer, one bar per entity (grouped: three categorical slots)."""
    entity = {"CUSTOMER": "Customer", "POLICY": "Policy", "CLAIM": "Claim"}
    df = pd.DataFrame([{
        "Layer": c["layer"], "Table": c["table"], "Records": c["total"],
        "Entity": next(v for k, v in entity.items() if k in c["table"]),
    } for c in counts])
    base = alt.Chart(df).encode(
        x=alt.X("Layer:N", title=None, sort=LAYER_ORDER, axis=alt.Axis(labelAngle=0, grid=False),
                scale=alt.Scale(paddingInner=0.25)),
        xOffset=alt.XOffset("Entity:N", sort=ENTITY_ORDER, scale=alt.Scale(paddingInner=0.08)),
        y=alt.Y("Records:Q", title="Records", axis=alt.Axis(format=",d")),
        tooltip=[alt.Tooltip("Table:N"), alt.Tooltip("Layer:N"), alt.Tooltip("Entity:N"),
                 alt.Tooltip("Records:Q", format=",")],
    )
    bars = base.mark_bar(cornerRadiusEnd=4).encode(
        color=alt.Color("Entity:N", scale=alt.Scale(domain=ENTITY_ORDER, range=ENTITY_COLOR)))
    labels = base.mark_text(dy=-7, color=MUTED, fontSize=11).encode(
        text=alt.Text("Records:Q", format=","))
    return _style(bars + labels, height=260)


# ------------------------------------------------------------- stat tiles
def stat_tiles_html(tiles: list[dict]) -> str:
    """
    A KPI row of tiles: [{"label", "value", "note", "tone"}], tone one of
    "accent" | "PASS" | "FAIL" | "ERROR" | "neutral". The coloured top edge
    and icon carry the tone; the text inherits the page's own colour, so the
    tiles read correctly in the light and the dark theme.
    """
    icons = {"PASS": "✓", "FAIL": "✕", "ERROR": "!", "accent": "●", "neutral": "●"}
    colors = {**STATUS_COLOR, "accent": ACCENT, "neutral": MUTED}
    cells = "".join(f"""
      <div style="background:rgba(128,128,128,0.06);border:1px solid rgba(128,128,128,0.22);
                  border-top:4px solid {colors[x['tone']]};border-radius:10px;padding:12px 14px;
                  color:inherit;">
        <div style="font:500 12px {_HTML_FONT};opacity:0.8;">
          <span style="color:{colors[x['tone']]};font-weight:700;margin-right:6px;">{icons[x['tone']]}</span>{x['label']}
        </div>
        <div style="font:600 28px/1.2 {_HTML_FONT};margin-top:6px;">{x['value']}</div>
        <div style="font:400 12px {_HTML_FONT};opacity:0.6;margin-top:2px;">{x.get('note', '')}</div>
      </div>""" for x in tiles)
    return ('<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(135px,1fr));'
            f'gap:12px;margin:4px 0 8px;">{cells}</div>')
