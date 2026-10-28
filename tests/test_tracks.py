from datetime import UTC, datetime, timedelta

import pytest

from uncharted.tracks import (
    Point,
    clean_segments,
    haversine_m,
    segments_to_geometry,
    semicircles_to_degrees,
)

T0 = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
# ~1.1 m per 1e-5 degrees of latitude.
DLAT_PER_M = 1 / 111_195


def walk(n: int, start_s: int = 0, lat0: float = 59.9, step_m: float = 3.0) -> list[Point]:
    """n points one second apart moving north at step_m m/s."""
    return [
        Point(T0 + timedelta(seconds=start_s + i), lat0 + i * step_m * DLAT_PER_M, 10.75)
        for i in range(n)
    ]


def test_semicircles() -> None:
    assert semicircles_to_degrees(2**31) == pytest.approx(180.0)
    assert semicircles_to_degrees(-(2**30)) == pytest.approx(-90.0)
    assert semicircles_to_degrees(714_000_000) == pytest.approx(59.846, abs=1e-3)


def test_haversine() -> None:
    assert haversine_m(59.9, 10.75, 59.9 + 100 * DLAT_PER_M, 10.75) == pytest.approx(100, rel=1e-3)


def test_clean_track_unchanged() -> None:
    pts = walk(10)
    assert clean_segments(pts) == [pts]


def test_spike_is_dropped() -> None:
    pts = walk(10)
    spike = Point(pts[5].t + timedelta(milliseconds=500), pts[5].lat + 100 * DLAT_PER_M, 10.75)
    segs = clean_segments([*pts, spike])
    assert segs == [pts]


def test_time_gap_splits() -> None:
    a = walk(5)
    b = walk(5, start_s=200, lat0=a[-1].lat)  # same place, long pause
    segs = clean_segments(a + b)
    assert len(segs) == 2


def test_distance_jump_splits() -> None:
    a = walk(5)
    # 30 s later, 1 km away: plausible-ish speed? no, 33 m/s, but > max_gap_m so it's a split
    b = walk(5, start_s=34, lat0=a[-1].lat + 1000 * DLAT_PER_M)
    segs = clean_segments(a + b)
    assert [len(s) for s in segs] == [5, 5]


def test_single_point_segments_discarded() -> None:
    a = walk(5)
    lone = walk(1, start_s=500)
    b = walk(3, start_s=1000)
    segs = clean_segments(a + lone + b)
    assert [len(s) for s in segs] == [5, 3]


def test_geometry() -> None:
    geom = segments_to_geometry([walk(3), walk(2, start_s=100)])
    assert geom is not None and len(geom.geoms) == 2
    assert segments_to_geometry([]) is None
