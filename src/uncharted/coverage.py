"""Coverage statistics: by length and by named-street completion, overall and per district."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

import geopandas as gpd
import pandas as pd


@dataclass
class CoverageStats:
    total_length_m: float
    covered_length_m: float
    length_coverage: float
    streets_total: int
    streets_completed: int
    street_completion: float


def join_coverage(edges: gpd.GeoDataFrame, coverage: pd.DataFrame) -> gpd.GeoDataFrame:
    cols = ["edge_id", "covered", "covered_fraction", "first_covered"]
    return edges.merge(pd.DataFrame(coverage[cols]), on="edge_id", how="left")


def _per_street(df: pd.DataFrame) -> pd.DataFrame:
    """Covered and total length per named street (index: name)."""
    named = df[df["name"].notna()]
    return (
        named.assign(cov_len=named["length_m"].where(named["covered"], 0.0))
        .groupby("name")[["cov_len", "length_m"]]
        .sum()
    )


def _stats(df: pd.DataFrame, street_threshold: float) -> CoverageStats:
    total = float(df["length_m"].sum())
    covered = float(df.loc[df["covered"], "length_m"].sum())
    per_street = _per_street(df)
    completed = int((per_street["cov_len"] >= street_threshold * per_street["length_m"]).sum())
    n = len(per_street)
    return CoverageStats(
        total_length_m=total,
        covered_length_m=covered,
        length_coverage=covered / total if total else 0.0,
        streets_total=n,
        streets_completed=completed,
        street_completion=completed / n if n else 0.0,
    )


def overall_stats(joined: pd.DataFrame, street_threshold: float = 0.9) -> CoverageStats:
    return _stats(joined, street_threshold)


def district_stats(joined: pd.DataFrame, street_threshold: float = 0.9) -> dict[str, CoverageStats]:
    """Per district. A street crossing districts is judged on its part within each district."""
    return {
        str(d): _stats(group, street_threshold)
        for d, group in joined[joined["district"].notna()].groupby("district")
    }


def coverage_timeline(joined: pd.DataFrame) -> pd.DataFrame:
    """Cumulative length coverage per day, from each edge's first_covered date."""
    total = joined["length_m"].sum()
    cov = joined[joined["covered"] & joined["first_covered"].notna()]
    if cov.empty:
        return pd.DataFrame(columns=["date", "covered_length_m", "length_coverage"])
    daily = cov.groupby(cov["first_covered"].dt.date)["length_m"].sum().sort_index().cumsum()
    return pd.DataFrame(
        {
            "date": [d.isoformat() for d in daily.index],
            "covered_length_m": daily.values,
            "length_coverage": daily.values / total,
        }
    )


def as_of(joined: pd.DataFrame, t: datetime) -> pd.DataFrame:
    """Coverage as it was just before `t`: edges first covered at or after `t` count as uncovered.
    Covered edges without a first_covered date are treated as old."""
    first = joined["first_covered"]
    out = joined.copy()
    out["covered"] = joined["covered"].fillna(False).astype(bool) & (first.isna() | (first < t))
    return out


@dataclass
class StreetProgress:
    name: str
    length_m: float
    before: float  # covered fraction at the start of the period
    after: float  # covered fraction at the end
    districts: list[str]


@dataclass
class PeriodSummary:
    start: datetime
    end: datetime
    before: CoverageStats
    after: CoverageStats
    new_length_m: float
    completed: list[StreetProgress]  # crossed street_threshold during the period
    progressed: list[StreetProgress]  # gained coverage but not (yet) completed
    districts: dict[str, tuple[CoverageStats, CoverageStats]]  # only those that changed


def period_summary(
    joined: pd.DataFrame, start: datetime, end: datetime, street_threshold: float = 0.9
) -> PeriodSummary:
    """What changed between `start` and `end`, from each edge's first_covered time."""
    before, after = as_of(joined, start), as_of(joined, end)
    new = after["covered"] & ~before["covered"]

    sb, sa = _per_street(before), _per_street(after)
    frac_b = sb["cov_len"] / sb["length_m"]
    frac_a = sa["cov_len"] / sa["length_m"]
    named = joined[joined["name"].notna()]
    districts = named.groupby("name")["district"].agg(lambda d: sorted(d.dropna().unique()))
    completed, progressed = [], []
    for name in frac_a.index[frac_a > frac_b]:
        p = StreetProgress(
            str(name),
            float(sa.at[name, "length_m"]),
            float(frac_b[name]),
            float(frac_a[name]),
            [str(d) for d in districts[name]],
        )
        if p.after >= street_threshold > p.before:
            completed.append(p)
        elif p.after < street_threshold:
            progressed.append(p)
    completed.sort(key=lambda p: -p.length_m)
    progressed.sort(key=lambda p: -(p.after - p.before) * p.length_m)

    db, da = district_stats(before, street_threshold), district_stats(after, street_threshold)
    changed = {d: (db[d], da[d]) for d in da if db[d].covered_length_m != da[d].covered_length_m}
    return PeriodSummary(
        start=start,
        end=end,
        before=_stats(before, street_threshold),
        after=_stats(after, street_threshold),
        new_length_m=float(joined.loc[new, "length_m"].sum()),
        completed=completed,
        progressed=progressed,
        districts=changed,
    )


def stats_dict(joined: pd.DataFrame, street_threshold: float = 0.9) -> dict:
    return {
        "overall": asdict(overall_stats(joined, street_threshold)),
        "districts": {k: asdict(v) for k, v in district_stats(joined, street_threshold).items()},
        "timeline": coverage_timeline(joined).to_dict(orient="records"),
    }
