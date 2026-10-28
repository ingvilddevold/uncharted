"""Settings loading: secrets from .env, everything else from config.toml (+ config.local.toml)."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Secrets(BaseSettings):
    """Values read from the environment / .env. Kept separate so they never end up in dumps."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    garmin_personal_api_key: SecretStr | None = None


class AreaConfig(BaseModel):
    place: str = "Oslo, Norway"
    crs: str = "EPSG:25832"
    district_admin_level: int = 9


class SyncConfig(BaseModel):
    activity_types: list[str] = Field(default_factory=lambda: ["running", "trail_running"])
    request_delay_s: float = 1.0


class MatchingConfig(BaseModel):
    method: str = "buffer"
    buffer_m: float = 20.0
    edge_threshold: float = 0.9
    street_threshold: float = 0.9


class TracksConfig(BaseModel):
    max_speed_mps: float = 10.0
    max_gap_s: float = 60.0
    max_gap_m: float = 200.0


class StreetsConfig(BaseModel):
    include_highway: list[str] = Field(
        default_factory=lambda: [
            "residential",
            "living_street",
            "unclassified",
            "tertiary",
            "secondary",
            "primary",
            "pedestrian",
        ]
    )
    exclude_access: list[str] = Field(default_factory=lambda: ["private"])
    include_footways: bool = False


class Config(BaseModel):
    area: AreaConfig = Field(default_factory=AreaConfig)
    sync: SyncConfig = Field(default_factory=SyncConfig)
    matching: MatchingConfig = Field(default_factory=MatchingConfig)
    tracks: TracksConfig = Field(default_factory=TracksConfig)
    streets: StreetsConfig = Field(default_factory=StreetsConfig)
    data_dir: Path = Path("data")

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def tracks_path(self) -> Path:
        return self.data_dir / "tracks" / "tracks.parquet"

    @property
    def edges_path(self) -> Path:
        return self.data_dir / "streets" / "edges.parquet"

    @property
    def districts_path(self) -> Path:
        return self.data_dir / "streets" / "districts.parquet"

    @property
    def coverage_path(self) -> Path:
        return self.data_dir / "coverage" / "edges_coverage.parquet"

    @property
    def output_dir(self) -> Path:
        return self.data_dir / "output"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "cache.db"

    @property
    def token_path(self) -> Path:
        return self.data_dir / "token.json"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: Path = Path("config.toml"), local_path: Path | None = None) -> Config:
    """Load config.toml, then merge config.local.toml (gitignored) on top if present."""
    if local_path is None:
        local_path = path.with_name("config.local.toml")
    data: dict[str, Any] = {}
    for p in (path, local_path):
        if p.exists():
            with p.open("rb") as f:
                data = _deep_merge(data, tomllib.load(f))
    return Config.model_validate(data)


def load_secrets() -> Secrets:
    return Secrets()
