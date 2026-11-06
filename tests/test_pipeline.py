"""End-to-end match → stats → render on the synthetic grid, without network or FIT files."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString, MultiLineString

from tests.conftest import CRS, pt
from uncharted import pipeline
from uncharted.config import Config
from uncharted.garmin.models import ActivitySummary
from uncharted.storage import Store


def fake_tracks(by_id: dict[int, list]) -> gpd.GeoDataFrame:
    rows = [
        {
            "activity_id": i,
            "start_time": datetime(2026, 1, i, tzinfo=UTC),
            "geometry": MultiLineString([LineString(c)]),
        }
        for i, c in by_id.items()
    ]
    cols = ["activity_id", "start_time", "geometry"]
    return gpd.GeoDataFrame(rows, columns=cols, geometry="geometry", crs=CRS).to_crs(4326)


def test_match_stats_render(tmp_path: Path, monkeypatch, grid_edges, grid_districts) -> None:
    cfg = Config(data_dir=tmp_path)
    cfg.matching.buffer_m = 10
    cfg.edges_path.parent.mkdir(parents=True)
    grid_edges.to_parquet(cfg.edges_path)
    grid_districts.to_parquet(cfg.districts_path)

    runs = {1: [pt(0, 0), pt(0, 100)], 2: [pt(0, 100), pt(200, 100)]}
    with Store(cfg.db_path) as store:
        for i in runs:
            store.mark_downloaded(
                ActivitySummary.model_validate(
                    {
                        "activityId": i,
                        "activityType": {"typeKey": "running"},
                        "distance": 1000,
                        "startTimeGMT": f"2026-01-0{i} 00:00:00",
                    }
                )
            )

    def fake_build(files, *_args):
        ids = [i for i, _ in files]
        return fake_tracks({i: runs[i] for i in ids})

    monkeypatch.setattr(pipeline, "build_tracks", fake_build)
    # Pretend FIT files exist.
    cfg.raw_dir.mkdir(parents=True)
    for i in runs:
        (cfg.raw_dir / f"{i}.fit").write_bytes(b"")

    r = pipeline.run_match(cfg, rebuild=False, log=lambda _m: None)
    assert (r.activities, r.covered_edges) == (2, 3)

    # Second run is a no-op.
    r = pipeline.run_match(cfg, rebuild=False, log=lambda _m: None)
    assert r.activities == 0 and r.covered_edges == 3

    stats = pipeline.run_stats(cfg)
    assert stats["overall"]["length_coverage"] == 0.75
    assert json.loads((cfg.output_dir / "stats.json").read_text())["districts"]["Vest"]

    html = Path(pipeline.run_render(cfg)).read_text()
    assert "Street coverage" in html
    assert "Alfa gate" in html
    assert pipeline.total_km_run(cfg) == 2.0

    # Report for the second day only: run 2 completes "Beta vei" (B + C).
    start = datetime(2026, 1, 2, tzinfo=UTC)
    out, text = pipeline.run_report(cfg, start, start + timedelta(days=1))
    assert out.exists()
    assert "1 runs, 1.0 km" in text
    assert "Completed: Beta vei" in text


def test_cached_footways_dropped_when_turned_off(grid_edges) -> None:
    edges = grid_edges.copy()
    edges.loc[0, "highway"] = "footway"
    cfg = Config()
    cfg.streets.include_footways = True
    assert len(pipeline._configured_edges(cfg, edges)) == 4
    cfg.streets.include_footways = False
    assert "footway" not in set(pipeline._configured_edges(cfg, edges)["highway"])
