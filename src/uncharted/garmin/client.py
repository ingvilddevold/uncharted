"""Thin read-only client for the endpoints we need.

Endpoints follow Garmin's official client (github.com/garmin/connect-personal-api-python):
  GET /activitylist-service/activities/search/activities?start=&limit=[&startDate=&endDate=]
  GET /download-service/files/activity/{id}   -> ZIP bytes containing the original FIT
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from datetime import date

import httpx

from uncharted.garmin import BASE_URL
from uncharted.garmin.auth import Authenticator
from uncharted.garmin.models import ActivitySummary, GarminApiError

LIST_PATH = "/activitylist-service/activities/search/activities"
DOWNLOAD_PATH = "/download-service/files/activity/{activity_id}"
DOWNLOAD_ACCEPT = "application/zip, application/x-zip-compressed, application/octet-stream"

RETRY_STATUSES = {429, 500, 502, 503, 504}


class GarminClient:
    def __init__(
        self,
        auth: Authenticator,
        http: httpx.Client,
        base_url: str = BASE_URL,
        max_retries: int = 5,
        backoff_base_s: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._auth = auth
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._backoff_base_s = backoff_base_s
        self._sleep = sleep

    def _get(self, path: str, params: dict | None = None, accept: str = "application/json"):
        """GET with one token refresh on 401 and exponential backoff on 429/5xx."""
        refreshed = False
        attempt = 0
        while True:
            token = self._auth.token()
            resp = self._http.get(
                f"{self._base_url}{path}",
                params=params,
                headers={"Authorization": f"Bearer {token.access_token}", "Accept": accept},
            )
            if resp.status_code == 401 and not refreshed:
                self._auth.token(force_refresh=True)
                refreshed = True
                continue
            if resp.status_code in RETRY_STATUSES and attempt < self._max_retries:
                retry_after = resp.headers.get("Retry-After")
                delay = (
                    float(retry_after)
                    if retry_after and retry_after.isdigit()
                    else self._backoff_base_s * 2**attempt
                )
                self._sleep(delay)
                attempt += 1
                continue
            if resp.status_code != 200:
                raise GarminApiError(resp.status_code, "GET", path)
            return resp

    def list_activities(
        self,
        start: int = 0,
        limit: int = 100,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[ActivitySummary]:
        params: dict[str, str | int] = {"start": start, "limit": limit}
        if start_date:
            params["startDate"] = start_date.isoformat()
            params["endDate"] = (end_date or date.today()).isoformat()
        resp = self._get(LIST_PATH, params=params)
        data = resp.json() if resp.content else []
        return [ActivitySummary.model_validate(item) for item in data or []]

    def iter_activities(
        self, page_size: int = 100, start_date: date | None = None
    ) -> Iterator[ActivitySummary]:
        start = 0
        while True:
            page = self.list_activities(start=start, limit=page_size, start_date=start_date)
            yield from page
            if len(page) < page_size:
                return
            start += page_size

    def download_activity(self, activity_id: int) -> bytes:
        """Return the original activity file as ZIP bytes."""
        path = DOWNLOAD_PATH.format(activity_id=int(activity_id))
        # Must include application/zip; octet-stream alone gets HTTP 406.
        resp = self._get(path, accept=DOWNLOAD_ACCEPT)
        if not resp.content:
            raise GarminApiError(204, "GET", path)
        return resp.content
