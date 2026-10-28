"""Street network and district boundaries from OpenStreetMap."""

from __future__ import annotations

from typing import Any

import geopandas as gpd
import pandas as pd

from uncharted.config import AreaConfig, StreetsConfig

FOOTWAY_TYPES = ["footway", "path", "cycleway"]
# Footways mapped as a street's sidewalk or crossing would double-count the street itself.
EXCLUDE_FOOTWAY = ["sidewalk", "crossing"]


def highway_types(cfg: StreetsConfig) -> list[str]:
    types = list(cfg.include_highway)
    if cfg.include_footways:
        types += [t for t in FOOTWAY_TYPES if t not in types]
    return types


def overpass_filter(cfg: StreetsConfig) -> str:
    """Overpass way filter; motorways, trunks and *_link are excluded by not being listed."""
    f = f'["highway"~"^({"|".join(highway_types(cfg))})$"]["area"!~"yes"]'
    if cfg.exclude_access:
        f += f'["access"!~"^({"|".join(cfg.exclude_access)})$"]'
    if cfg.include_footways:
        f += f'["footway"!~"^({"|".join(EXCLUDE_FOOTWAY)})$"]'
    return f


def _first(value: Any) -> Any:
    # Simplified osmnx edges hold lists when several OSM ways were merged into one edge.
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _as_str(value: Any) -> str | None:
    if isinstance(value, list):
        return ";".join(str(v) for v in value)
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return str(value)


def filter_edges(edges: gpd.GeoDataFrame, cfg: StreetsConfig) -> gpd.GeoDataFrame:
    """Defensive re-application of the highway/access filter (Overpass already did most of it)."""
    allowed = set(highway_types(cfg))
    excluded = set(cfg.exclude_access)

    def keep(row: pd.Series) -> bool:
        hw = row.get("highway")
        hws = hw if isinstance(hw, list) else [hw]
        if not any(h in allowed for h in hws):
            return False
        acc = row.get("access")
        accs = acc if isinstance(acc, list) else [acc]
        return not any(a in excluded for a in accs)

    return edges[edges.apply(keep, axis=1)]


def dedup_edges(edges: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Keep one edge per undirected (u, v) pair and geometry.

    osmnx returns both u→v and v→u for two-way streets. Parallel edges between the same nodes
    with different geometry (e.g. a loop around a block) are distinct and must be kept, so the
    key includes the rounded length too.
    """
    df = edges.copy()
    lo = df[["u", "v"]].min(axis=1)
    hi = df[["u", "v"]].max(axis=1)
    df["_k"] = list(zip(lo, hi, df["length"].round(1), strict=True))
    return df.drop_duplicates("_k").drop(columns="_k")


def prepare_edges(raw: gpd.GeoDataFrame, streets: StreetsConfig, crs: str) -> gpd.GeoDataFrame:
    """osmnx edge GeoDataFrame → tidy edges in the metric CRS with an integer edge_id."""
    df = raw.reset_index()
    df = filter_edges(df, streets)
    df = dedup_edges(df)
    df = df.to_crs(crs)
    out = gpd.GeoDataFrame(
        {
            "u": df["u"].astype("int64"),
            "v": df["v"].astype("int64"),
            "osmid": df["osmid"].map(_as_str),
            "name": df.get("name", pd.Series(index=df.index, dtype=object)).map(_first),
            "highway": df["highway"].map(_first),
            "length_m": df.geometry.length,
        },
        geometry=df.geometry.values,
        crs=crs,
    ).reset_index(drop=True)
    out["name"] = out["name"].where(out["name"].notna(), None)
    out.insert(0, "edge_id", range(len(out)))
    return out


def assign_districts(edges: gpd.GeoDataFrame, districts: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Give each edge the district containing its midpoint (None if outside all districts)."""
    mid = gpd.GeoDataFrame(
        {"edge_id": edges["edge_id"]},
        geometry=edges.geometry.interpolate(0.5, normalized=True),
        crs=edges.crs,
    )
    joined = gpd.sjoin(mid, districts[["district", "geometry"]].to_crs(edges.crs), how="left")
    joined = joined.drop_duplicates("edge_id").set_index("edge_id")["district"]
    out = edges.copy()
    out["district"] = out["edge_id"].map(joined)
    return out


def download_edges(area: AreaConfig, streets: StreetsConfig) -> gpd.GeoDataFrame:
    import osmnx as ox

    graph = ox.graph_from_place(
        area.place, custom_filter=overpass_filter(streets), retain_all=True, simplify=True
    )
    raw = ox.graph_to_gdfs(graph, nodes=False)
    return prepare_edges(raw, streets, area.crs)


def download_districts(area: AreaConfig) -> gpd.GeoDataFrame:
    import osmnx as ox

    feats = ox.features_from_place(
        area.place,
        tags={"boundary": "administrative", "admin_level": str(area.district_admin_level)},
    )
    feats = feats[feats.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
    feats = feats[feats["admin_level"].astype(str) == str(area.district_admin_level)]
    out = gpd.GeoDataFrame(
        {"district": feats["name"].values}, geometry=feats.geometry.values, crs=feats.crs
    )
    return out.dissolve("district").reset_index().to_crs(area.crs)
