from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.conftest import pt
from tests.test_matching_coverage import D1, D2, tracks
from uncharted.coverage import CoverageStats, PeriodSummary, join_coverage, period_summary
from uncharted.matching import apply_tracks, empty_coverage
from uncharted.report import PeriodRuns, period, render_report, summary_text

DAY = timedelta(days=1)


@pytest.fixture
def two_runs(grid_edges):
    """D1 covers A (Alfa gate) and B (half of Beta vei); D2 covers C (rest of Beta vei) and D."""
    runs = tracks(
        (D1, [pt(0, 0), pt(0, 100), pt(100, 100)]),
        (D2, [pt(100, 0), pt(100, 100), pt(200, 100)]),
    )
    cov = apply_tracks(grid_edges, empty_coverage(grid_edges), runs, buffer_m=10)
    return join_coverage(grid_edges, cov), runs


def test_period_summary_second_run(two_runs) -> None:
    joined, _ = two_runs
    s = period_summary(joined, D2 - DAY, D2 + DAY)
    assert s.before.length_coverage == pytest.approx(0.5)
    assert s.after.length_coverage == pytest.approx(1.0)
    assert s.new_length_m == pytest.approx(200)
    assert [p.name for p in s.completed] == ["Beta vei"]
    assert s.completed[0].before == pytest.approx(0.5)
    assert s.completed[0].districts == ["Vest", "Øst"]
    assert s.progressed == []
    assert (s.before.streets_completed, s.after.streets_completed) == (1, 2)
    assert set(s.districts) == {"Øst"}  # Vest was already fully run


def test_period_summary_first_run(two_runs) -> None:
    joined, _ = two_runs
    s = period_summary(joined, D1 - DAY, D1 + DAY)
    assert [p.name for p in s.completed] == ["Alfa gate"]
    assert [(p.name, p.after) for p in s.progressed] == [("Beta vei", pytest.approx(0.5))]


def test_period_with_no_runs_changes_nothing(two_runs) -> None:
    joined, _ = two_runs
    s = period_summary(joined, D2 + DAY, D2 + 2 * DAY)
    assert s.new_length_m == 0
    assert s.completed == s.progressed == []
    assert s.districts == {}


def test_default_period_is_last_full_week() -> None:
    start, end = period(date(2026, 10, 28))  # a Wednesday
    assert (start.date(), end.date()) == (date(2026, 10, 19), date(2026, 10, 26))
    assert start.tzinfo is not None


def test_period_since_until_inclusive() -> None:
    start, end = period(date(2026, 10, 28), since=date(2026, 10, 1), until=date(2026, 10, 3))
    assert (start.date(), end.date()) == (date(2026, 10, 1), date(2026, 10, 4))
    _, end = period(date(2026, 10, 28), since=date(2026, 10, 1))
    assert end.date() == date(2026, 10, 29)


def test_week_label_across_dst_change() -> None:
    # 19–25 Oct 2026 ends on the day European clocks go back, so it lasts 7 days + 1 hour;
    # local_midnight gives fixed offsets like these. Still "Week 43".
    start = datetime(2026, 10, 19, tzinfo=timezone(timedelta(hours=2)))
    end = datetime(2026, 10, 26, tzinfo=timezone(timedelta(hours=1)))
    zero = CoverageStats(1, 0, 0, 1, 0, 0)
    s = PeriodSummary(start, end, zero, zero, 0, [], [], {})
    assert summary_text(s, PeriodRuns(0, 0), "X").startswith("Week 43")


def test_render_report(tmp_path: Path, two_runs) -> None:
    joined, runs = two_runs
    s = period_summary(joined, D2 - DAY, D2 + DAY)
    week = runs[runs["start_time"] == D2]
    out = render_report(
        joined, week, s, PeriodRuns(1, 3000), "Testby", "EPSG:25832", tmp_path / "r.html"
    )
    html = out.read_text()
    assert "Beta vei" in html
    assert "<iframe" in html
    assert "3.0 km" in html

    # No GPS runs: no map, still a page.
    out = render_report(joined, None, s, PeriodRuns(0, 0), "Testby", "EPSG:25832", out)
    assert "<iframe" not in out.read_text()
