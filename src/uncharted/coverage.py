"""Coverage statistics: by length and by named-street completion, overall and per district."""

from __future__ import annotations

from dataclasses import asdict, dataclass

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


def _stats(df: pd.DataFrame, street_threshold: float) -> CoverageStats:
    total = float(df["length_m"].sum())
    covered = float(df.loc[df["covered"], "length_m"].sum())
    named = df[df["name"].notna()]
    per_street = (
        named.assign(cov_len=named["length_m"].where(named["covered"], 0.0))
        .groupby("name")[["cov_len", "length_m"]]
        .sum()
    )
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


def stats_dict(joined: pd.DataFrame, street_threshold: float = 0.9) -> dict:
    return {
        "overall": asdict(overall_stats(joined, street_threshold)),
        "districts": {k: asdict(v) for k, v in district_stats(joined, street_threshold).items()},
        "timeline": coverage_timeline(joined).to_dict(orient="records"),
    }
