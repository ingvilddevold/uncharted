from datetime import UTC, datetime

import geopandas as gpd
import pytest
from shapely.geometry import LineString, MultiLineString

from tests.conftest import CRS, pt
from uncharted.coverage import (
    coverage_timeline,
    district_stats,
    join_coverage,
    overall_stats,
)
from uncharted.matching import apply_tracks, empty_coverage

D1 = datetime(2026, 3, 1, 7, 0, tzinfo=UTC)
D2 = datetime(2026, 4, 1, 7, 0, tzinfo=UTC)


def tracks(*items: tuple[datetime, list[tuple[float, float]]]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"activity_id": range(len(items)), "start_time": [t for t, _ in items]},
        geometry=[MultiLineString([LineString(coords)]) for _, coords in items],
        crs=CRS,
    )


def test_full_and_partial_coverage(grid_edges) -> None:
    # Run along A (x=0, 5 m off-centre) and half of B.
    run = tracks((D1, [pt(5, -10), pt(5, 100), pt(50, 100)]))
    cov = apply_tracks(grid_edges, empty_coverage(grid_edges), run, buffer_m=20)
    assert list(cov["covered"]) == [True, False, False, False]
    assert cov.loc[1, "covered_fraction"] == pytest.approx(0.45, abs=0.01)  # 5..50 m of B
    assert cov.loc[0, "first_covered"] == D1


def test_partial_runs_add_up_incrementally(grid_edges) -> None:
    cov = empty_coverage(grid_edges)
    cov = apply_tracks(grid_edges, cov, tracks((D1, [pt(0, 100), pt(40, 100)])), buffer_m=10)
    assert not cov.loc[1, "covered"]
    cov = apply_tracks(grid_edges, cov, tracks((D2, [pt(40, 100), pt(100, 100)])), buffer_m=10)
    assert cov.loc[1, "covered"]
    assert cov.loc[1, "covered_fraction"] == pytest.approx(1.0)
    assert cov.loc[1, "first_covered"] == D2


def test_passing_the_end_of_a_side_street_does_not_cover_it(grid_edges) -> None:
    # Run along B and C, a few metres off-centre, past the top of D. The buffer reaches 20 m
    # into D, but the run never goes along it.
    run = tracks((D1, [pt(0, 103), pt(200, 103)]))
    cov = apply_tracks(grid_edges, empty_coverage(grid_edges), run, buffer_m=20)
    assert list(cov["covered"]) == [False, True, True, False]
    assert cov.loc[3, "covered_length_m"] < 1.0


def test_overlapping_runs_do_not_double_count(grid_edges) -> None:
    run = [pt(0, 100), pt(100, 100)]
    cov = apply_tracks(
        grid_edges, empty_coverage(grid_edges), tracks((D1, run), (D2, run)), buffer_m=10
    )
    assert cov.loc[1, "covered_length_m"] == pytest.approx(100.0, abs=0.1)
    assert cov.loc[1, "first_covered"] == D1


def test_stats(grid_edges) -> None:
    # Covers A, B and C fully; D untouched.
    run = tracks((D1, [pt(0, 0), pt(0, 100), pt(200, 100)]))
    cov = apply_tracks(grid_edges, empty_coverage(grid_edges), run, buffer_m=10)
    assert list(cov["covered"]) == [True, True, True, False]

    joined = join_coverage(grid_edges, cov)
    s = overall_stats(joined)
    assert s.total_length_m == 400
    assert s.length_coverage == pytest.approx(0.75)
    assert (s.streets_completed, s.streets_total) == (2, 2)  # D is unnamed

    per = district_stats(joined)
    assert per["Vest"].length_coverage == 1.0
    assert per["Øst"].length_coverage == pytest.approx(0.5)
    assert sum(d.total_length_m for d in per.values()) == s.total_length_m

    tl = coverage_timeline(joined)
    assert list(tl["date"]) == ["2026-03-01"]
    assert tl["length_coverage"].iloc[-1] == pytest.approx(0.75)


def test_street_completion_needs_whole_street(grid_edges) -> None:
    # Only half of "Beta vei" (edge B, not C).
    run = tracks((D1, [pt(0, 100), pt(100, 100)]))
    cov = apply_tracks(grid_edges, empty_coverage(grid_edges), run, buffer_m=5)
    s = overall_stats(join_coverage(grid_edges, cov))
    assert s.streets_completed == 0
