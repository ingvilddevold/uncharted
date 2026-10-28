"""Download running activities that we don't have yet."""

from __future__ import annotations

import io
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from uncharted.garmin.client import GarminClient
from uncharted.storage import Store


@dataclass
class SyncResult:
    seen: int = 0
    matched_type: int = 0
    downloaded: int = 0
    skipped_existing: int = 0


def extract_fit(zip_bytes: bytes, dest: Path) -> Path:
    """Extract the (first) .fit file from a Garmin download ZIP to `dest`."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        fits = [n for n in zf.namelist() if n.lower().endswith(".fit")]
        if not fits:
            raise ValueError("download ZIP contains no .fit file")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(zf.read(fits[0]))
    return dest


def fit_path(raw_dir: Path, activity_id: int) -> Path:
    return raw_dir / f"{activity_id}.fit"


def sync_activities(
    client: GarminClient,
    store: Store,
    raw_dir: Path,
    activity_types: set[str],
    since: date | None = None,
    full: bool = False,
    request_delay_s: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
    on_progress: Callable[[str], None] = lambda _msg: None,
) -> SyncResult:
    result = SyncResult()
    have = set() if full else store.downloaded_ids()
    raw_dir.mkdir(parents=True, exist_ok=True)

    for act in client.iter_activities(start_date=since):
        result.seen += 1
        if act.type_key not in activity_types:
            continue
        result.matched_type += 1
        if act.activity_id in have:
            result.skipped_existing += 1
            continue
        if result.downloaded:
            sleep(request_delay_s)
        data = client.download_activity(act.activity_id)
        (raw_dir / f"{act.activity_id}.zip").write_bytes(data)
        extract_fit(data, fit_path(raw_dir, act.activity_id))
        store.mark_downloaded(act)
        result.downloaded += 1
        on_progress(f"downloaded {act.activity_id} ({act.type_key})")
    return result
