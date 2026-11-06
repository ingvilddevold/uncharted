"""Weekly (or any period) report: what the period's runs added, as a standalone HTML page."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

import folium
import geopandas as gpd
import pandas as pd
from shapely.geometry import box

from uncharted.coverage import PeriodSummary, StreetProgress
from uncharted.render import TRACK, UNCOVERED, _to_web

NEW = "#d7301f"
OLD = "#fdbb84"
# Margin around the period's runs when cropping the street network for the report map.
CROP_MARGIN_M = 500
MAX_PROGRESSED = 10


@dataclass
class PeriodRuns:
    count: int
    distance_m: float


def local_midnight(d: date) -> datetime:
    """Start of day `d` in the machine's local time zone (DST-aware)."""
    return datetime(d.year, d.month, d.day).astimezone()


def period(
    today: date, since: date | None = None, until: date | None = None
) -> tuple[datetime, datetime]:
    """[start, end) for a report. Default: the last full Monday–Sunday week before `today`.
    With `since`, from that day through `until` (inclusive, default today)."""
    if since is None:
        monday = today - timedelta(days=today.weekday() + 7)
        return local_midnight(monday), local_midnight(monday + timedelta(days=7))
    return local_midnight(since), local_midnight((until or today) + timedelta(days=1))


def _period_label(s: PeriodSummary) -> str:
    last = s.end - timedelta(seconds=1)
    # Compare dates, not durations: a week across a DST change is 7 days ± 1 hour.
    if (s.end.date() - s.start.date()).days == 7 and s.start.weekday() == 0:
        head = f"Week {s.start.isocalendar().week}"
    else:
        head = "Your runs"
    return f"{head} · {s.start:%-d %b} – {last:%-d %b %Y}"


def _pp(after: float, before: float) -> str:
    d = (after - before) * 100
    return f"+{d:.1f} pp" if d >= 0.05 else "no change"


def summary_text(s: PeriodSummary, runs: PeriodRuns, place: str) -> str:
    """Short plain-text version, for the terminal or a notification."""
    a, b = s.after, s.before
    lines = [
        f"{_period_label(s)} — {place}",
        f"{runs.count} runs, {runs.distance_m / 1000:.1f} km; "
        f"{s.new_length_m / 1000:.1f} km of new streets",
        f"Coverage {a.length_coverage:.1%} ({_pp(a.length_coverage, b.length_coverage)}), "
        f"{a.streets_completed} / {a.streets_total} streets "
        f"(+{a.streets_completed - b.streets_completed})",
    ]
    if s.completed:
        lines.append("Completed: " + ", ".join(p.name for p in s.completed))
    return "\n".join(lines)


def _report_map(
    joined: gpd.GeoDataFrame, tracks: gpd.GeoDataFrame, s: PeriodSummary, metric_crs: str
) -> str:
    """Folium map of the area around the period's runs, as a full HTML document."""
    area = box(*tracks.to_crs(metric_crs).total_bounds).buffer(CROP_MARGIN_M)
    edges = joined.to_crs(metric_crs)
    edges = edges[edges.intersects(area)]
    first = edges["first_covered"]
    covered = edges["covered"].fillna(False).astype(bool)
    is_new = covered & (first >= s.start) & (first < s.end)
    is_old = covered & ~is_new & (first.isna() | (first < s.start))
    edges = _to_web(edges[["name", "geometry"]].assign(new=is_new, old=is_old), metric_crs)
    edges["name"] = edges["name"].fillna("(unnamed)")

    fmap = folium.Map(tiles=None, control_scale=True)
    folium.TileLayer("OpenStreetMap", opacity=0.45, control=False).add_to(fmap)
    b = tracks.total_bounds
    fmap.fit_bounds([[b[1], b[0]], [b[3], b[2]]])
    layers = [
        (~edges["new"] & ~edges["old"], "Not yet run", UNCOVERED, 2),
        (edges["old"], "Run before", OLD, 3),
        (edges["new"], "New this period", NEW, 5),
    ]
    for mask, name, colour, weight in layers:
        if mask.any():
            folium.GeoJson(
                edges.loc[mask, ["name", "geometry"]],
                name=name,
                style_function=lambda _f, c=colour, w=weight: {"color": c, "weight": w},
                tooltip=folium.GeoJsonTooltip(["name"], aliases=[""]),
            ).add_to(fmap)
    folium.GeoJson(
        _to_web(tracks[["geometry"]], metric_crs),
        name="GPS tracks",
        style_function=lambda _f: {"color": TRACK, "weight": 1.5, "opacity": 0.6},
    ).add_to(fmap)
    fmap.get_root().header.add_child(
        folium.Element("<style>.leaflet-interactive:focus { outline: none; }</style>")
    )
    folium.LayerControl(collapsed=True).add_to(fmap)
    return fmap.get_root().render()


def _street_rows(streets: list[StreetProgress], show_bar: bool) -> str:
    rows = []
    for p in streets:
        where = (
            f'<span class="muted">{escape(", ".join(p.districts))}</span>' if p.districts else ""
        )
        bar = (
            f'<div class="bar"><span style="width:{p.before:.0%}" class="was"></span>'
            f'<span style="width:{p.after - p.before:.0%}" class="now"></span></div>'
            if show_bar
            else ""
        )
        right = f"{p.before:.0%} → <b>{p.after:.0%}</b>" if show_bar else f"{p.length_m:,.0f} m"
        rows.append(
            f'<li><div class="row"><span>{escape(p.name)} {where}</span>'
            f"<span>{right}</span></div>{bar}</li>"
        )
    return "<ul>" + "".join(rows) + "</ul>"


CSS = """
:root { --bg: #f6f5f2; --card: #fff; --text: #222; --muted: #777; --line: #e4e2dd;
        --accent: #d7301f; --old: #fdbb84; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #17171a; --card: #222226; --text: #eee; --muted: #999; --line: #333;
          --accent: #ff6b4a; --old: #8a5a3c; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
       font: 15px/1.5 system-ui, -apple-system, sans-serif; }
main { max-width: 760px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 26px; margin: 0; }
h2 { font-size: 17px; margin: 28px 0 8px; }
.muted { color: var(--muted); font-size: 13px; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
         gap: 10px; margin: 18px 0; }
.card { background: var(--card); border-radius: 10px; padding: 12px 14px; }
.card .big { font-size: 24px; font-weight: 650; }
.card .delta { color: var(--accent); font-size: 13px; font-weight: 600; }
iframe { width: 100%; height: 60vh; min-height: 320px; border: 0; border-radius: 10px;
         background: var(--card); }
ul { list-style: none; padding: 0; margin: 0; background: var(--card); border-radius: 10px; }
li { padding: 9px 14px; border-bottom: 1px solid var(--line); }
li:last-child { border-bottom: 0; }
.row { display: flex; justify-content: space-between; gap: 12px; }
.bar { display: flex; height: 5px; margin-top: 6px; background: var(--line); border-radius: 3px;
       overflow: hidden; }
.bar .was { background: var(--old); } .bar .now { background: var(--accent); }
table { width: 100%; border-collapse: collapse; background: var(--card); border-radius: 10px;
        overflow: hidden; }
td, th { padding: 8px 14px; text-align: left; border-bottom: 1px solid var(--line); }
th { font-size: 13px; color: var(--muted); font-weight: 500; }
td.n, th.n { text-align: right; }
.empty { background: var(--card); border-radius: 10px; padding: 14px; color: var(--muted); }
"""


def render_report(
    joined: gpd.GeoDataFrame,
    tracks: gpd.GeoDataFrame | None,
    s: PeriodSummary,
    runs: PeriodRuns,
    place: str,
    metric_crs: str,
    out_path: Path,
) -> Path:
    """Write the report page to `out_path`."""
    a, b = s.after, s.before
    cards = [
        ("Runs", f"{runs.count}", f"{runs.distance_m / 1000:.1f} km"),
        ("New streets", f"{s.new_length_m / 1000:.1f} km", "first time run"),
        ("Coverage", f"{a.length_coverage:.1%}", _pp(a.length_coverage, b.length_coverage)),
        (
            "Streets completed",
            f"{a.streets_completed}",
            f"+{a.streets_completed - b.streets_completed} of {a.streets_total}",
        ),
    ]
    cards_html = "".join(
        f'<div class="card"><div class="muted">{t}</div><div class="big">{v}</div>'
        f'<div class="delta">{escape(d)}</div></div>'
        for t, v, d in cards
    )

    has_tracks = tracks is not None and not tracks.empty
    if has_tracks:
        doc = _report_map(joined, tracks, s, metric_crs)
        map_html = f'<iframe title="Map of this period\'s runs" srcdoc="{escape(doc)}"></iframe>'
    else:
        map_html = '<div class="empty">No runs with GPS in this period.</div>'

    if s.completed:
        completed_html = _street_rows(s.completed, show_bar=False)
    else:
        completed_html = '<div class="empty">No streets completed this time.</div>'

    progressed = s.progressed[:MAX_PROGRESSED]
    progressed_html = (
        "<h2>Making progress</h2>" + _street_rows(progressed, show_bar=True) if progressed else ""
    )

    districts_html = ""
    if s.districts:
        rows = "".join(
            f"<tr><td>{escape(name)}</td>"
            f'<td class="n">{db.length_coverage:.1%} → <b>{da.length_coverage:.1%}</b></td>'
            f'<td class="n">+{(da.covered_length_m - db.covered_length_m) / 1000:.1f} km</td>'
            f'<td class="n">{da.streets_completed} / {da.streets_total}</td></tr>'
            # Biggest gain first.
            for name, (db, da) in sorted(
                s.districts.items(),
                key=lambda kv: kv[1][0].covered_length_m - kv[1][1].covered_length_m,
            )
        )
        districts_html = (
            "<h2>Districts</h2><table><tr><th>District</th><th class='n'>Coverage</th>"
            f"<th class='n'>New</th><th class='n'>Streets</th></tr>{rows}</table>"
        )

    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(_period_label(s))}</title>
<style>{CSS}</style>
</head>
<body>
<main>
  <h1>{escape(_period_label(s))}</h1>
  <div class="muted">{escape(place)}</div>
  <div class="cards">{cards_html}</div>
  {map_html}
  <h2>Streets completed</h2>
  {completed_html}
  {progressed_html}
  {districts_html}
  <p class="muted">Map data © OpenStreetMap contributors.</p>
</main>
</body>
</html>
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page)
    return out_path


def tracks_in_period(tracks: gpd.GeoDataFrame | None, s: PeriodSummary) -> gpd.GeoDataFrame | None:
    if tracks is None or tracks.empty:
        return None
    t = pd.to_datetime(tracks["start_time"], utc=True)
    return tracks[(t >= s.start) & (t < s.end)]
