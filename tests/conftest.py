"""Tiny synthetic street grid in a made-up metric CRS location (no real places or tracks).

Layout (metres, EPSG:25832, arbitrary origin):

    (0,100) ---- B ---- (100,100) ---- C ---- (200,100)
       |                    |
       A                    D
       |                    |
    (0,0)                (100,0)

A: "Alfa gate" 100 m, B: "Beta vei" 100 m, C: "Beta vei" 100 m, D: unnamed 100 m.
"""

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Polygon

CRS = "EPSG:25832"
X0, Y0 = 600_000, 6_600_000


def pt(x: float, y: float) -> tuple[float, float]:
    return (X0 + x, Y0 + y)


@pytest.fixture
def grid_edges() -> gpd.GeoDataFrame:
    geoms = [
        LineString([pt(0, 0), pt(0, 100)]),
        LineString([pt(0, 100), pt(100, 100)]),
        LineString([pt(100, 100), pt(200, 100)]),
        LineString([pt(100, 0), pt(100, 100)]),
    ]
    return gpd.GeoDataFrame(
        {
            "edge_id": [0, 1, 2, 3],
            "u": [1, 2, 3, 4],
            "v": [2, 3, 5, 3],
            "name": ["Alfa gate", "Beta vei", "Beta vei", None],
            "highway": ["residential"] * 4,
            "length_m": [100.0] * 4,
            "district": ["Vest", "Vest", "Øst", "Øst"],
        },
        geometry=geoms,
        crs=CRS,
    )


@pytest.fixture
def grid_districts() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"district": ["Vest", "Øst"]},
        geometry=[
            Polygon([pt(-50, -50), pt(60, -50), pt(60, 150), pt(-50, 150)]),
            Polygon([pt(60, -50), pt(250, -50), pt(250, 150), pt(60, 150)]),
        ],
        crs=CRS,
    )
