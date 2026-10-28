"""Interactive folium map of covered vs. uncovered streets."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

import folium
import geopandas as gpd
import pandas as pd

from uncharted.coverage import CoverageStats, coverage_timeline

UNCOVERED = "#b8b8b8"
COVERED = "#e4572e"
TRACK = "#2e86ab"
# Covered edges by first-covered year, oldest → newest.
YEAR_COLOURS = ["#fdd49e", "#fdbb84", "#fc8d59", "#ef6548", "#d7301f", "#b30000", "#7f0000"]

# Simplification tolerance in metres; keeps the HTML small without visible distortion.
SIMPLIFY_M = 2.0


def _to_web(gdf: gpd.GeoDataFrame, metric_crs: str) -> gpd.GeoDataFrame:
    out = gdf.to_crs(metric_crs)
    out = out.set_geometry(out.geometry.simplify(SIMPLIFY_M))
    return out.to_crs(4326)


def _year_colour(years: pd.Series) -> dict[int, str]:
    uniq = sorted(int(y) for y in years.dropna().unique())
    pal = YEAR_COLOURS[-len(uniq) :] if len(uniq) <= len(YEAR_COLOURS) else YEAR_COLOURS
    return {y: pal[min(i, len(pal) - 1)] for i, y in enumerate(uniq)}


def _overlay_html(s: CoverageStats, km_run: float) -> str:
    return f"""
    <div style="position: fixed; top: 12px; left: 56px; z-index: 1000; background: white;
                padding: 10px 14px; border-radius: 6px; box-shadow: 0 1px 4px rgba(0,0,0,.3);
                font: 13px/1.45 system-ui, sans-serif; color: #222;">
      <div style="font-weight: 600; font-size: 15px; margin-bottom: 4px;">Street coverage</div>
      <div>Length covered: <b>{s.length_coverage:.1%}</b>
           ({s.covered_length_m / 1000:.0f} / {s.total_length_m / 1000:.0f} km)</div>
      <div>Streets completed: <b>{s.streets_completed}</b> / {s.streets_total}
           ({s.street_completion:.1%})</div>
      <div>Total km run: <b>{km_run:,.0f}</b></div>
    </div>"""


def _timeline_html(timeline: pd.DataFrame) -> str:
    """Small inline-SVG chart of cumulative length coverage, with a hover readout."""
    if len(timeline) < 2:
        return ""
    w, h, pad_l, pad_r, pad_t, pad_b = 280, 120, 34, 10, 8, 20
    dates = pd.to_datetime(timeline["date"])
    t0, t1 = dates.iloc[0].timestamp(), dates.iloc[-1].timestamp()
    ymax = float(timeline["length_coverage"].max()) or 1.0
    xs = [pad_l + (d.timestamp() - t0) / (t1 - t0) * (w - pad_l - pad_r) for d in dates]
    ys = [pad_t + (1 - v / ymax) * (h - pad_t - pad_b) for v in timeline["length_coverage"]]
    # Cumulative coverage only changes on run days, so draw it as steps.
    pts = [(xs[0], ys[0])]
    for x, y, py in zip(xs[1:], ys[1:], ys[:-1], strict=True):
        pts += [(x, py), (x, y)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    base = h - pad_b
    area = f"{pad_l:.1f},{base} {line} {xs[-1]:.1f},{base}"
    cov, km = timeline["length_coverage"], timeline["covered_length_m"] / 1000
    data = json.dumps(
        [
            [round(x, 1), round(y, 1), f"{d.day} {d:%b %Y}", f"{v:.1%}", f"{k:.0f} km"]
            for x, y, d, v, k in zip(xs, ys, dates, cov, km, strict=True)
        ]
    )
    first, last = f"{dates.iloc[0]:%b %Y}", f"{dates.iloc[-1]:%b %Y}"
    return f"""
    <div id="tm-timeline" style="position: fixed; bottom: 24px; right: 12px; z-index: 1000;
         background: white; padding: 8px 10px; border-radius: 6px;
         box-shadow: 0 1px 4px rgba(0,0,0,.3); font: 12px system-ui, sans-serif; color: #222;">
      <div style="font-weight: 600; margin-bottom: 2px;">Length covered over time</div>
      <svg width="{w}" height="{h}" style="display: block; overflow: visible;">
        <line x1="{pad_l}" x2="{w - pad_r}" y1="{base}" y2="{base}" stroke="#ccc"/>
        <line x1="{pad_l}" x2="{w - pad_r}" y1="{pad_t}" y2="{pad_t}" stroke="#eee"/>
        <text x="{pad_l - 4}" y="{pad_t + 4}" text-anchor="end" fill="#666">{ymax:.0%}</text>
        <text x="{pad_l - 4}" y="{base + 4}" text-anchor="end" fill="#666">0%</text>
        <text x="{pad_l}" y="{h - 4}" fill="#666">{first}</text>
        <text x="{w - pad_r}" y="{h - 4}" text-anchor="end" fill="#666">{last}</text>
        <polygon points="{area}" fill="{COVERED}" fill-opacity="0.12"/>
        <polyline points="{line}" fill="none" stroke="{COVERED}" stroke-width="2"
                  stroke-linejoin="round"/>
        <line class="x" y1="{pad_t}" y2="{base}" stroke="#888" stroke-dasharray="3 3"
              visibility="hidden"/>
        <circle class="dot" r="4" fill="{COVERED}" stroke="white" stroke-width="2"
                visibility="hidden"/>
        <rect x="{pad_l}" y="0" width="{w - pad_l - pad_r}" height="{base}" fill="transparent"/>
      </svg>
      <div class="readout" style="color: #555; min-height: 1.4em;"></div>
    </div>
    <script>
    (function () {{
      var pts = {data}, box = document.getElementById("tm-timeline"),
          svg = box.querySelector("svg"), x = svg.querySelector(".x"),
          dot = svg.querySelector(".dot"), out = box.querySelector(".readout");
      svg.addEventListener("mousemove", function (e) {{
        var mx = e.clientX - svg.getBoundingClientRect().left, p = pts[0];
        for (var i = 0; i < pts.length && pts[i][0] <= mx; i++) p = pts[i];
        x.setAttribute("x1", p[0]); x.setAttribute("x2", p[0]);
        dot.setAttribute("cx", p[0]); dot.setAttribute("cy", p[1]);
        x.setAttribute("visibility", "visible"); dot.setAttribute("visibility", "visible");
        out.innerHTML = p[2] + ": <b>" + p[3] + "</b> (" + p[4] + ")";
      }});
      svg.addEventListener("mouseleave", function () {{
        x.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden");
        out.textContent = "";
      }});
    }})();
    </script>"""


def render_map(
    joined: gpd.GeoDataFrame,
    districts: gpd.GeoDataFrame | None,
    tracks: gpd.GeoDataFrame | None,
    overall: CoverageStats,
    per_district: dict[str, CoverageStats],
    km_run: float,
    metric_crs: str,
    out_path: Path,
    colour_by_year: bool = True,
    show_timeline: bool = True,
) -> Path:
    """Write the map to `out_path`."""
    edges = _to_web(joined[["edge_id", "name", "covered", "first_covered", "geometry"]], metric_crs)
    edges["name"] = edges["name"].fillna("(unnamed)")
    edges["year"] = edges["first_covered"].dt.year
    edges = edges.drop(columns="first_covered")

    bounds = edges.total_bounds
    # CartoDB tiles need an API key now; faded OSM tiles give a similar light base without one.
    fmap = folium.Map(tiles=None, control_scale=True)
    folium.TileLayer("OpenStreetMap", opacity=0.45, control=False).add_to(fmap)
    fmap.fit_bounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]])

    if districts is not None and not districts.empty:
        d = districts.to_crs(4326).copy()
        d["coverage"] = d["district"].map(
            lambda n: f"{per_district[n].length_coverage:.1%}" if n in per_district else "–"
        )
        d["streets"] = d["district"].map(
            lambda n: (
                f"{per_district[n].streets_completed} / {per_district[n].streets_total}"
                if n in per_district
                else "–"
            )
        )
        folium.GeoJson(
            d[["district", "coverage", "streets", "geometry"]],
            name="Districts",
            style_function=lambda _f: {
                "color": "#555",
                "weight": 1,
                "fillOpacity": 0.03,
                "dashArray": "4 4",
            },
            tooltip=folium.GeoJsonTooltip(
                ["district", "coverage", "streets"], aliases=["District", "Length", "Streets"]
            ),
        ).add_to(fmap)

    folium.GeoJson(
        edges.loc[~edges["covered"], ["name", "geometry"]],
        name="Uncovered streets",
        style_function=lambda _f: {"color": UNCOVERED, "weight": 2, "opacity": 0.8},
        tooltip=folium.GeoJsonTooltip(["name"], aliases=[""]),
    ).add_to(fmap)

    cov = edges.loc[edges["covered"]].copy()
    colours = _year_colour(cov["year"]) if colour_by_year else {}
    cov["colour"] = cov["year"].map(
        lambda y: colours.get(int(y), COVERED) if pd.notna(y) else COVERED
    )
    cov["year"] = cov["year"].map(lambda y: str(int(y)) if pd.notna(y) else "")
    folium.GeoJson(
        cov[["name", "year", "colour", "geometry"]],
        name="Covered streets",
        style_function=lambda f: {
            "color": f["properties"]["colour"],
            "weight": 3.5,
            "opacity": 0.95,
        },
        tooltip=folium.GeoJsonTooltip(["name", "year"], aliases=["", "First run"]),
    ).add_to(fmap)

    if tracks is not None and not tracks.empty:
        t = _to_web(tracks[["activity_id", "geometry"]], metric_crs)
        folium.GeoJson(
            t,
            name="GPS tracks",
            show=False,
            style_function=lambda _f: {"color": TRACK, "weight": 1.5, "opacity": 0.5},
        ).add_to(fmap)

    if colours:
        items = "".join(
            f'<div><span style="display:inline-block;width:14px;height:4px;background:{c};'
            f'vertical-align:middle;margin-right:6px"></span>{escape(str(y))}</div>'
            for y, c in colours.items()
        )
        fmap.get_root().html.add_child(
            folium.Element(
                '<div style="position: fixed; bottom: 24px; left: 12px; z-index: 1000; '
                "background: white; padding: 8px 12px; border-radius: 6px; "
                'box-shadow: 0 1px 4px rgba(0,0,0,.3); font: 12px system-ui, sans-serif;">'
                f"<b>First run</b>{items}</div>"
            )
        )

    # Clicked paths get a browser focus ring drawn around their bounding box; hide it.
    fmap.get_root().header.add_child(
        folium.Element("<style>.leaflet-interactive:focus { outline: none; }</style>")
    )
    fmap.get_root().html.add_child(folium.Element(_overlay_html(overall, km_run)))
    if show_timeline:
        fmap.get_root().html.add_child(folium.Element(_timeline_html(coverage_timeline(joined))))
    folium.LayerControl(collapsed=False).add_to(fmap)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fmap.save(str(out_path))
    return out_path
