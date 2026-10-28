"""Buffer-method matching: an edge is covered where a track runs along it within `buffer_m`.

The track is clipped to each edge's buffer and projected onto the edge; the covered stretch is
the span of those projections. Testing the edge against the track's buffer instead would mark
short side streets as run whenever a run passes their end.

Per edge we keep the covered *part* of its geometry (union over all runs so far), so partial
coverage from several runs adds up and new runs can be merged in incrementally.
"""

from __future__ import annotations

from collections import defaultdict

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.ops import substring

# Snap unions to 1 cm so slivers from float noise don't accumulate between runs.
GRID_SIZE = 0.01

COVERAGE_COLUMNS = ["edge_id", "covered_length_m", "covered_fraction", "covered", "first_covered"]


def _utc(t: object) -> pd.Timestamp:
    ts = pd.Timestamp(t)
    return ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC")


def empty_coverage(edges: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """One row per edge, nothing covered. Geometry holds the covered part (empty at first)."""
    return gpd.GeoDataFrame(
        {
            "edge_id": edges["edge_id"].values,
            "covered_length_m": 0.0,
            "covered_fraction": 0.0,
            "covered": False,
            "first_covered": pd.Series(pd.NaT, index=edges.index, dtype="datetime64[ns, UTC]"),
        },
        geometry=shapely.empty(len(edges), geom_type=shapely.GeometryType.LINESTRING),
        crs=edges.crs,
    ).reset_index(drop=True)


def _covered_parts(
    edge_geoms: np.ndarray, edge_bufs: np.ndarray, idx: np.ndarray, track: object
) -> np.ndarray:
    """For each edge in `idx`, the stretch(es) of it that `track` runs alongside."""
    clipped = shapely.intersection(track, edge_bufs[idx])
    pieces, owner = shapely.get_parts(clipped, return_index=True)
    is_line = shapely.get_type_id(pieces) == shapely.GeometryType.LINESTRING
    pieces, owner = pieces[is_line], owner[is_line]
    out = shapely.empty(len(idx), geom_type=shapely.GeometryType.LINESTRING)
    if len(pieces) == 0:
        return out

    coords, piece_of = shapely.get_coordinates(pieces, return_index=True)
    pos = shapely.line_locate_point(edge_geoms[idx[owner[piece_of]]], shapely.points(coords))
    lo = np.full(len(pieces), np.inf)
    hi = np.full(len(pieces), -np.inf)
    np.minimum.at(lo, piece_of, pos)
    np.maximum.at(hi, piece_of, pos)

    stretches = defaultdict(list)
    for o, a, b in zip(owner, lo, hi, strict=True):
        if b - a > GRID_SIZE:
            stretches[o].append(substring(edge_geoms[idx[o]], a, b))
    for o, lines in stretches.items():
        out[o] = shapely.union_all(lines, grid_size=GRID_SIZE)
    return out


def apply_tracks(
    edges: gpd.GeoDataFrame,
    coverage: gpd.GeoDataFrame,
    tracks: gpd.GeoDataFrame,
    buffer_m: float = 20.0,
    edge_threshold: float = 0.9,
) -> gpd.GeoDataFrame:
    """Merge `tracks` into `coverage` and return the updated coverage (input is not modified).

    `edges` and `coverage` must be row-aligned (same edge_id order). Tracks are processed in
    start_time order, so `first_covered` is the start time of the run that pushed the edge over
    the threshold.
    """
    if not (edges["edge_id"].values == coverage["edge_id"].values).all():
        raise ValueError("edges and coverage are not aligned; re-run with --rebuild")

    cov = coverage.copy()
    edge_geoms = edges.geometry.values
    lengths = edges["length_m"].to_numpy(dtype=float)
    covered_geoms = np.asarray(cov.geometry.values, dtype=object).copy()
    covered = cov["covered"].to_numpy().copy()
    first = cov["first_covered"].copy()

    tracks = tracks.to_crs(edges.crs).sort_values("start_time")
    tree = shapely.STRtree(edge_geoms)
    edge_bufs = None
    for start_time, track in zip(tracks["start_time"], tracks.geometry, strict=True):
        if track is None or track.is_empty:
            continue
        idx = tree.query(track, predicate="dwithin", distance=buffer_m)
        if len(idx) == 0:
            continue
        if edge_bufs is None:
            edge_bufs = shapely.buffer(edge_geoms, buffer_m)
            shapely.prepare(edge_bufs)
        parts = _covered_parts(edge_geoms, edge_bufs, idx, track)
        covered_geoms[idx] = shapely.union(covered_geoms[idx], parts, grid_size=GRID_SIZE)
        frac = np.minimum(shapely.length(covered_geoms[idx]) / lengths[idx], 1.0)
        newly = idx[(frac >= edge_threshold) & ~covered[idx]]
        covered[newly] = True
        first.iloc[newly] = _utc(start_time)

    covered_len = shapely.length(covered_geoms)
    cov["covered_length_m"] = covered_len
    cov["covered_fraction"] = np.minimum(covered_len / lengths, 1.0)
    cov["covered"] = covered
    cov["first_covered"] = first
    cov = cov.set_geometry(gpd.GeoSeries(covered_geoms, crs=edges.crs, index=cov.index))
    return cov
