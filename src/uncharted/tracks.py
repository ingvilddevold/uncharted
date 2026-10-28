"""FIT -> cleaned (Multi)LineString per activity."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import fitdecode
import geopandas as gpd
from shapely.geometry import LineString, MultiLineString

SEMICIRCLE_TO_DEG = 180 / 2**31
EARTH_RADIUS_M = 6_371_008.8


@dataclass(frozen=True)
class Point:
    t: datetime
    lat: float
    lon: float


def semicircles_to_degrees(value: int) -> float:
    return value * SEMICIRCLE_TO_DEG


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def read_fit_points(path: Path) -> list[Point]:
    """Return GPS points from `record` messages, skipping those without a position."""
    points: list[Point] = []
    with fitdecode.FitReader(path) as fit:
        for frame in fit:
            if not isinstance(frame, fitdecode.FitDataMessage) or frame.name != "record":
                continue
            lat = frame.get_value("position_lat", fallback=None)
            lon = frame.get_value("position_long", fallback=None)
            t = frame.get_value("timestamp", fallback=None)
            if lat is None or lon is None or t is None:
                continue
            points.append(Point(t, semicircles_to_degrees(lat), semicircles_to_degrees(lon)))
    return points


def clean_segments(
    points: Sequence[Point],
    max_speed_mps: float = 10.0,
    max_gap_s: float = 60.0,
    max_gap_m: float = 200.0,
) -> list[list[Point]]:
    """Split on long gaps, drop GPS spikes, and discard segments shorter than two points.

    Each point is compared to the last *kept* point: a time or distance jump beyond the gap
    limits starts a new segment; otherwise an implied speed above `max_speed_mps` is a spike.
    """
    segments: list[list[Point]] = []
    current: list[Point] = []
    for p in sorted(points, key=lambda p: p.t):
        if not current:
            current.append(p)
            continue
        prev = current[-1]
        dt = (p.t - prev.t).total_seconds()
        d = haversine_m(prev.lat, prev.lon, p.lat, p.lon)
        if dt > max_gap_s or d > max_gap_m:
            segments.append(current)
            current = [p]
        elif dt <= 0 or d / dt > max_speed_mps:
            continue
        else:
            current.append(p)
    segments.append(current)
    return [s for s in segments if len(s) >= 2]


def segments_to_geometry(segments: Iterable[Sequence[Point]]) -> MultiLineString | None:
    lines = [LineString([(p.lon, p.lat) for p in s]) for s in segments]
    return MultiLineString(lines) if lines else None


def build_tracks(
    fit_files: Iterable[tuple[int, Path]],
    max_speed_mps: float = 10.0,
    max_gap_s: float = 60.0,
    max_gap_m: float = 200.0,
) -> gpd.GeoDataFrame:
    """Read FIT files and return a GeoDataFrame (activity_id, start_time, geometry) in EPSG:4326."""
    rows = []
    for activity_id, path in fit_files:
        points = read_fit_points(path)
        geom = segments_to_geometry(clean_segments(points, max_speed_mps, max_gap_s, max_gap_m))
        if geom is None:
            continue
        rows.append({"activity_id": activity_id, "start_time": points[0].t, "geometry": geom})
    return gpd.GeoDataFrame(
        rows, columns=["activity_id", "start_time", "geometry"], geometry="geometry", crs=4326
    )
