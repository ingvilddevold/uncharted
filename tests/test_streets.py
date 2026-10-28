import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString

from uncharted.config import StreetsConfig
from uncharted.streets import (
    assign_districts,
    dedup_edges,
    filter_edges,
    overpass_filter,
    prepare_edges,
)


def raw_osmnx_edges() -> gpd.GeoDataFrame:
    """Mimics ox.graph_to_gdfs(G, nodes=False): (u, v, key) index, EPSG:4326, list-valued tags."""
    a = LineString([(10.70, 59.90), (10.701, 59.90)])
    b = LineString([(10.701, 59.90), (10.701, 59.901)])
    gdf = gpd.GeoDataFrame(
        {
            "u": [1, 2, 2, 3, 5],
            "v": [2, 1, 3, 2, 6],
            "key": [0, 0, 0, 0, 0],
            "osmid": [11, 11, [12, 13], [12, 13], 14],
            "name": ["Gate", "Gate", ["Vei", "Vei2"], ["Vei", "Vei2"], None],
            "highway": ["residential", "residential", "tertiary", "tertiary", "motorway"],
            "access": [None, None, None, None, None],
            "length": [55.7, 55.7, 111.2, 111.2, 300.0],
        },
        geometry=[a, a.reverse(), b, b.reverse(), a],
        crs=4326,
    )
    return gdf.set_index(["u", "v", "key"])


def test_overpass_filter() -> None:
    cfg = StreetsConfig(include_highway=["residential", "primary"], exclude_access=["private"])
    f = overpass_filter(cfg)
    assert '"highway"~"^(residential|primary)$"' in f
    assert '"access"!~"^(private)$"' in f
    assert "footway" not in f
    with_footways = overpass_filter(StreetsConfig(include_footways=True))
    assert "footway" in with_footways
    assert '"footway"!~"^(sidewalk|crossing)$"' in with_footways


def test_filter_edges_access_and_type() -> None:
    df = raw_osmnx_edges().reset_index()
    df.loc[0, "access"] = "private"
    out = filter_edges(df, StreetsConfig())
    assert 0 not in out.index  # private
    assert 4 not in out.index  # motorway


def test_dedup_two_way() -> None:
    df = raw_osmnx_edges().reset_index()
    out = dedup_edges(df)
    assert len(out) == 3  # 1-2, 2-3, 5-6


def test_dedup_keeps_parallel_edges() -> None:
    df = raw_osmnx_edges().reset_index().iloc[[0]]
    other = df.copy()
    other["length"] = 80.0  # same nodes, different route
    out = dedup_edges(gpd.GeoDataFrame(pd.concat([df, other], ignore_index=True), crs=4326))
    assert len(out) == 2


def test_prepare_edges() -> None:
    out = prepare_edges(raw_osmnx_edges(), StreetsConfig(), "EPSG:25832")
    assert list(out["edge_id"]) == [0, 1]
    assert out.crs.to_epsg() == 25832
    assert set(out["name"]) == {"Gate", "Vei"}
    assert out.loc[out["name"] == "Vei", "osmid"].item() == "12;13"
    assert abs(out.loc[out["name"] == "Gate", "length_m"].item() - 55.7) < 1.0


def test_assign_districts(grid_edges, grid_districts) -> None:
    out = assign_districts(grid_edges.drop(columns="district"), grid_districts)
    # Edge 1 runs x=0..100: midpoint x=50 is in Vest.
    assert list(out["district"]) == ["Vest", "Vest", "Øst", "Øst"]
