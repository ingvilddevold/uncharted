"""Glue between the CLI and the individual steps: file I/O and incremental bookkeeping."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import geopandas as gpd
import httpx
import pandas as pd

from uncharted import coverage as cov_mod
from uncharted.config import Config, Secrets
from uncharted.garmin.auth import Authenticator, Token
from uncharted.garmin.client import GarminClient
from uncharted.matching import apply_tracks, empty_coverage
from uncharted.render import render_map
from uncharted.report import PeriodRuns, render_report, summary_text, tracks_in_period
from uncharted.storage import Store
from uncharted.streets import (
    assign_districts,
    download_districts,
    download_edges,
    highway_types,
)
from uncharted.sync import SyncResult, fit_path, sync_activities
from uncharted.tracks import build_tracks

Log = Callable[[str], None]


def _authenticator(cfg: Config, secrets: Secrets, http: httpx.Client) -> Authenticator:
    key = secrets.garmin_personal_api_key
    return Authenticator(key.get_secret_value() if key else "", cfg.token_path, http)


def auth_check(cfg: Config, secrets: Secrets) -> Token:
    with httpx.Client(timeout=30) as http:
        return _authenticator(cfg, secrets, http).token(force_refresh=True)


def run_sync(cfg: Config, secrets: Secrets, since: date | None, full: bool, log: Log) -> SyncResult:
    with httpx.Client(timeout=60) as http, Store(cfg.db_path) as store:
        client = GarminClient(_authenticator(cfg, secrets, http), http)
        return sync_activities(
            client,
            store,
            cfg.raw_dir,
            set(cfg.sync.activity_types),
            since=since,
            full=full,
            request_delay_s=cfg.sync.request_delay_s,
            on_progress=log,
        )


def _configured_edges(cfg: Config, edges: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Drop cached edges whose highway type the current config excludes (e.g. footways turned
    off after a download with them on). Turning a type on still needs `streets --refresh`."""
    return edges[edges["highway"].isin(highway_types(cfg.streets))]


def run_streets(cfg: Config, refresh: bool, log: Log) -> gpd.GeoDataFrame:
    if cfg.edges_path.exists() and not refresh:
        log(f"using cached {cfg.edges_path} (pass --refresh to re-download)")
        return _configured_edges(cfg, gpd.read_parquet(cfg.edges_path))
    import osmnx as ox

    # Keep osmnx's raw Overpass cache inside the gitignored data dir, not ./cache.
    ox.settings.cache_folder = str(cfg.data_dir / "osmnx_cache")
    # A cached Overpass response would defeat --refresh.
    ox.settings.use_cache = not refresh
    log(f"downloading street network for {cfg.area.place} …")
    edges = download_edges(cfg.area, cfg.streets)
    log(f"downloading districts (admin_level={cfg.area.district_admin_level}) …")
    districts = download_districts(cfg.area)
    edges = assign_districts(edges, districts)
    cfg.edges_path.parent.mkdir(parents=True, exist_ok=True)
    edges.to_parquet(cfg.edges_path)
    districts.to_parquet(cfg.districts_path)
    # Edge ids are not stable across downloads, so existing coverage no longer applies.
    if cfg.coverage_path.exists():
        cfg.coverage_path.unlink()
        with Store(cfg.db_path) as store:
            store.reset_processed()
        log("street network changed: coverage will be rebuilt on next match")
    return edges


def _update_tracks(cfg: Config, ids: list[int]) -> gpd.GeoDataFrame:
    """Extract tracks for `ids` from their FIT files and upsert them into tracks.parquet."""
    t = cfg.tracks
    new = build_tracks(
        ((i, fit_path(cfg.raw_dir, i)) for i in ids if fit_path(cfg.raw_dir, i).exists()),
        t.max_speed_mps,
        t.max_gap_s,
        t.max_gap_m,
    )
    if cfg.tracks_path.exists():
        old = gpd.read_parquet(cfg.tracks_path)
        old = old[~old["activity_id"].isin(ids)]
        all_tracks = gpd.GeoDataFrame(pd.concat([old, new], ignore_index=True), crs=4326)
    else:
        all_tracks = new
    cfg.tracks_path.parent.mkdir(parents=True, exist_ok=True)
    all_tracks.to_parquet(cfg.tracks_path)
    return new


@dataclass
class MatchResult:
    activities: int
    with_gps: int
    covered_edges: int
    total_edges: int


def run_match(cfg: Config, rebuild: bool, log: Log) -> MatchResult:
    if not cfg.edges_path.exists():
        raise FileNotFoundError("no street network yet; run `uncharted streets` first")
    edges = gpd.read_parquet(cfg.edges_path)

    with Store(cfg.db_path) as store:
        coverage = None
        if cfg.coverage_path.exists() and not rebuild:
            coverage = gpd.read_parquet(cfg.coverage_path)
            if not coverage["edge_id"].equals(edges["edge_id"]):
                log("coverage does not match the street network; rebuilding")
                coverage = None
        if coverage is None:
            store.reset_processed()
            coverage = empty_coverage(edges)

        ids = [r["id"] for r in store.unprocessed()]
        log(f"{len(ids)} activities to process")
        tracks = _update_tracks(cfg, ids)
        coverage = apply_tracks(
            edges, coverage, tracks, cfg.matching.buffer_m, cfg.matching.edge_threshold
        )
        cfg.coverage_path.parent.mkdir(parents=True, exist_ok=True)
        coverage.to_parquet(cfg.coverage_path)
        store.mark_processed(ids)

    return MatchResult(len(ids), len(tracks), int(coverage["covered"].sum()), len(edges))


def load_joined(cfg: Config) -> gpd.GeoDataFrame:
    if not cfg.coverage_path.exists():
        raise FileNotFoundError("no coverage yet; run `uncharted match` first")
    edges = _configured_edges(cfg, gpd.read_parquet(cfg.edges_path))
    coverage = gpd.read_parquet(cfg.coverage_path)
    return cov_mod.join_coverage(edges, coverage)


def run_stats(cfg: Config) -> dict:
    stats = cov_mod.stats_dict(load_joined(cfg), cfg.matching.street_threshold)
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    (cfg.output_dir / "stats.json").write_text(json.dumps(stats, indent=2, ensure_ascii=False))
    return stats


def total_km_run(cfg: Config) -> float:
    with Store(cfg.db_path) as store:
        return sum((r["distance_m"] or 0) for r in store.all_activities()) / 1000


def run_report(cfg: Config, start: datetime, end: datetime) -> tuple[Path, str]:
    """Write the period report; returns its path and a plain-text summary."""
    joined = load_joined(cfg)
    s = cov_mod.period_summary(joined, start, end, cfg.matching.street_threshold)
    with Store(cfg.db_path) as store:
        acts = [
            r
            for r in store.all_activities()
            if r["start_time"] and start <= datetime.fromisoformat(r["start_time"]) < end
        ]
    runs = PeriodRuns(len(acts), sum(r["distance_m"] or 0 for r in acts))
    tracks = gpd.read_parquet(cfg.tracks_path) if cfg.tracks_path.exists() else None
    out = render_report(
        joined,
        tracks_in_period(tracks, s),
        s,
        runs,
        cfg.area.place,
        cfg.area.crs,
        cfg.output_dir / f"report_{start:%Y-%m-%d}.html",
    )
    return out, summary_text(s, runs, cfg.area.place)


def run_render(cfg: Config) -> str:
    joined = load_joined(cfg)
    thr = cfg.matching.street_threshold
    districts = gpd.read_parquet(cfg.districts_path) if cfg.districts_path.exists() else None
    tracks = gpd.read_parquet(cfg.tracks_path) if cfg.tracks_path.exists() else None
    out = render_map(
        joined,
        districts,
        tracks,
        cov_mod.overall_stats(joined, thr),
        cov_mod.district_stats(joined, thr),
        total_km_run(cfg),
        cfg.area.crs,
        cfg.output_dir / "coverage_map.html",
    )
    return str(out)
