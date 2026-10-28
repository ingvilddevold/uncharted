"""Response models. Only the fields we use are declared; everything else is ignored."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class GarminApiError(Exception):
    """HTTP failure from the Personal API. Deliberately carries no response body or headers."""

    def __init__(self, status_code: int, method: str, path: str) -> None:
        self.status_code = status_code
        super().__init__(f"{method} {path} failed with HTTP {status_code}")


class ActivitySummary(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    activity_id: int = Field(alias="activityId")
    start_time_gmt: datetime | None = Field(default=None, alias="startTimeGMT")
    type_key: str | None = Field(default=None, alias="activityType")
    distance_m: float | None = Field(default=None, alias="distance")
    duration_s: float | None = Field(default=None, alias="duration")

    @field_validator("type_key", mode="before")
    @classmethod
    def _type_key(cls, v: Any) -> str | None:
        # The API nests the type: {"typeKey": "running", "typeId": 1, ...}
        if isinstance(v, dict):
            return v.get("typeKey")
        return v

    @field_validator("start_time_gmt", mode="before")
    @classmethod
    def _parse_gmt(cls, v: Any) -> Any:
        # Format is "YYYY-MM-DD HH:MM:SS" without a zone; it is UTC.
        if isinstance(v, str):
            return datetime.fromisoformat(v.replace(" ", "T")).replace(tzinfo=UTC)
        return v
