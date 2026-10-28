import base64
import io
import json
import zipfile
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from uncharted.garmin import BASE_URL, TOKEN_PATH
from uncharted.garmin.auth import Authenticator, jwt_expiry
from uncharted.garmin.client import DOWNLOAD_PATH, LIST_PATH, GarminClient
from uncharted.garmin.models import ActivitySummary, GarminApiError
from uncharted.storage import Store
from uncharted.sync import sync_activities

NOW = 1_800_000_000.0


def make_jwt(exp: float) -> str:
    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    return f"{b64({'alg': 'none'})}.{b64({'exp': exp})}.sig"


def make_zip(name: str = "1_ACTIVITY.fit", content: bytes = b"FITDATA") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, content)
    return buf.getvalue()


def activity(aid: int, type_key: str = "running") -> dict:
    return {
        "activityId": aid,
        "startTimeGMT": "2026-05-01 06:30:00",
        "activityType": {"typeKey": type_key, "typeId": 1},
        "distance": 5000.0,
        "duration": 1500.0,
        "somethingElse": "ignored",
    }


@pytest.fixture
def http():
    with httpx.Client() as c:
        yield c


def make_auth(tmp_path: Path, http: httpx.Client) -> Authenticator:
    return Authenticator("secret-key", tmp_path / "token.json", http, clock=lambda: NOW)


def test_jwt_expiry() -> None:
    assert jwt_expiry(make_jwt(123.0)) == 123.0
    with pytest.raises(ValueError):
        jwt_expiry("not-a-jwt")


@respx.mock
def test_token_is_cached_on_disk(tmp_path: Path, http: httpx.Client) -> None:
    route = respx.post(f"{BASE_URL}{TOKEN_PATH}").respond(text=make_jwt(NOW + 86400))
    tok = make_auth(tmp_path, http).token()
    assert tok.expires_at == NOW + 86400
    assert route.calls[0].request.headers["Authorization"] == "ApiKey secret-key"
    assert (tmp_path / "token.json").stat().st_mode & 0o077 == 0

    # New authenticator instance reads the cache instead of hitting the endpoint.
    make_auth(tmp_path, http).token()
    assert route.call_count == 1


@respx.mock
def test_expired_cache_is_refetched(tmp_path: Path, http: httpx.Client) -> None:
    route = respx.post(f"{BASE_URL}{TOKEN_PATH}")
    route.side_effect = [
        httpx.Response(200, text=make_jwt(NOW + 60)),  # inside refresh margin
        httpx.Response(200, text=make_jwt(NOW + 86400)),
    ]
    make_auth(tmp_path, http).token()
    tok = make_auth(tmp_path, http).token()
    assert route.call_count == 2
    assert tok.expires_at == NOW + 86400


@respx.mock
def test_token_error_does_not_leak_body(tmp_path: Path, http: httpx.Client) -> None:
    respx.post(f"{BASE_URL}{TOKEN_PATH}").respond(403, text="secret-key is revoked")
    with pytest.raises(GarminApiError) as exc:
        make_auth(tmp_path, http).token()
    assert exc.value.status_code == 403
    assert "secret" not in str(exc.value)


@respx.mock
def test_401_triggers_single_refresh(tmp_path: Path, http: httpx.Client) -> None:
    token_route = respx.post(f"{BASE_URL}{TOKEN_PATH}")
    token_route.side_effect = [
        httpx.Response(200, text=make_jwt(NOW + 86400)),
        httpx.Response(200, text=make_jwt(NOW + 90000)),
    ]
    list_route = respx.get(f"{BASE_URL}{LIST_PATH}")
    list_route.side_effect = [httpx.Response(401), httpx.Response(200, json=[activity(1)])]

    client = GarminClient(make_auth(tmp_path, http), http, sleep=lambda _s: None)
    acts = client.list_activities()
    assert [a.activity_id for a in acts] == [1]
    assert token_route.call_count == 2


@respx.mock
def test_429_backs_off_then_succeeds(tmp_path: Path, http: httpx.Client) -> None:
    respx.post(f"{BASE_URL}{TOKEN_PATH}").respond(text=make_jwt(NOW + 86400))
    list_route = respx.get(f"{BASE_URL}{LIST_PATH}")
    list_route.side_effect = [
        httpx.Response(429),
        httpx.Response(503),
        httpx.Response(200, json=[]),
    ]
    sleeps: list[float] = []
    client = GarminClient(make_auth(tmp_path, http), http, backoff_base_s=1, sleep=sleeps.append)
    assert client.list_activities() == []
    assert sleeps == [1, 2]


@respx.mock
def test_pagination_and_since(tmp_path: Path, http: httpx.Client) -> None:
    respx.post(f"{BASE_URL}{TOKEN_PATH}").respond(text=make_jwt(NOW + 86400))
    list_route = respx.get(f"{BASE_URL}{LIST_PATH}")
    list_route.side_effect = [
        httpx.Response(200, json=[activity(1), activity(2)]),
        httpx.Response(200, json=[activity(3)]),
    ]
    client = GarminClient(make_auth(tmp_path, http), http)
    ids = [a.activity_id for a in client.iter_activities(page_size=2, start_date=date(2026, 1, 1))]
    assert ids == [1, 2, 3]
    params = [dict(c.request.url.params) for c in list_route.calls]
    assert params[0]["start"] == "0" and params[1]["start"] == "2"
    assert params[0]["startDate"] == "2026-01-01"


def test_activity_summary_parsing() -> None:
    a = ActivitySummary.model_validate(activity(42, "trail_running"))
    assert a.activity_id == 42
    assert a.type_key == "trail_running"
    assert a.start_time_gmt is not None and a.start_time_gmt.tzinfo is not None


@respx.mock
def test_sync_is_incremental(tmp_path: Path, http: httpx.Client) -> None:
    respx.post(f"{BASE_URL}{TOKEN_PATH}").respond(text=make_jwt(NOW + 86400))
    respx.get(f"{BASE_URL}{LIST_PATH}").respond(
        json=[activity(1), activity(2, "cycling"), activity(3, "trail_running")]
    )
    dl = respx.get(url__regex=rf"{BASE_URL}{DOWNLOAD_PATH.format(activity_id=r'\d+')}").respond(
        content=make_zip()
    )
    client = GarminClient(make_auth(tmp_path, http), http)
    raw = tmp_path / "raw"
    types = {"running", "trail_running"}

    with Store(tmp_path / "cache.db") as store:
        r1 = sync_activities(client, store, raw, types, sleep=lambda _s: None)
        assert r1.downloaded == 2
        assert (raw / "1.fit").read_bytes() == b"FITDATA"
        assert not (raw / "2.fit").exists()

        r2 = sync_activities(client, store, raw, types, sleep=lambda _s: None)
        assert r2.downloaded == 0
        assert r2.skipped_existing == 2
    assert dl.call_count == 2
    assert "application/zip" in dl.calls[0].request.headers["Accept"]
