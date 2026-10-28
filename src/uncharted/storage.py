"""SQLite bookkeeping for activities. Geometries live in GeoParquet files (see config paths)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from uncharted.garmin.models import ActivitySummary

SCHEMA = """
CREATE TABLE IF NOT EXISTS activities (
    id            INTEGER PRIMARY KEY,
    start_time    TEXT,
    type          TEXT,
    distance_m    REAL,
    duration_s    REAL,
    downloaded_at TEXT,
    processed_at  TEXT
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def downloaded_ids(self) -> set[int]:
        rows = self.conn.execute("SELECT id FROM activities WHERE downloaded_at IS NOT NULL")
        return {r["id"] for r in rows}

    def mark_downloaded(self, a: ActivitySummary) -> None:
        start = a.start_time_gmt.isoformat() if a.start_time_gmt else None
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO activities (id, start_time, type, distance_m, duration_s, downloaded_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    start_time = excluded.start_time,
                    type = excluded.type,
                    distance_m = excluded.distance_m,
                    duration_s = excluded.duration_s,
                    downloaded_at = excluded.downloaded_at,
                    processed_at = NULL
                """,
                (a.activity_id, start, a.type_key, a.distance_m, a.duration_s, _now()),
            )

    def unprocessed(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM activities WHERE downloaded_at IS NOT NULL AND processed_at IS NULL"
                " ORDER BY start_time"
            )
        )

    def mark_processed(self, ids: Iterable[int]) -> None:
        now = _now()
        with self.conn:
            self.conn.executemany(
                "UPDATE activities SET processed_at = ? WHERE id = ?", [(now, i) for i in ids]
            )

    def reset_processed(self) -> None:
        with self.conn:
            self.conn.execute("UPDATE activities SET processed_at = NULL")

    def all_activities(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM activities ORDER BY start_time"))
