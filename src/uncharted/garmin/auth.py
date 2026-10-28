"""Exchange the Personal API key for a short-lived bearer token, with an on-disk cache.

The token endpoint answers with a bare JWT (text, not JSON); its expiry is the `exp` claim.
Tokens live 24 h (or until the key expires). There are no refresh tokens: re-exchange the key.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from uncharted.garmin import BASE_URL, TOKEN_PATH
from uncharted.garmin.models import GarminApiError

# Treat tokens as expired this long before their real expiry.
REFRESH_MARGIN_S = 10 * 60


@dataclass(frozen=True)
class Token:
    access_token: str = field(repr=False)
    expires_at: float


def jwt_expiry(token: str) -> float:
    """Return the `exp` claim of a JWT without verifying the signature."""
    try:
        payload = token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return float(data["exp"])
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("token endpoint did not return a JWT with an exp claim") from exc


class Authenticator:
    def __init__(
        self,
        api_key: str,
        cache_path: Path,
        http: httpx.Client,
        base_url: str = BASE_URL,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not api_key:
            raise ValueError("GARMIN_PERSONAL_API_KEY is not set (see .env.example)")
        self._key = api_key
        self._cache_path = cache_path
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._clock = clock
        # Only reuse a cached token issued for this same key and host.
        self._fingerprint = hashlib.sha256(f"{self._base_url}\0{api_key}".encode()).hexdigest()
        self._token: Token | None = None

    def token(self, force_refresh: bool = False) -> Token:
        if not force_refresh:
            tok = self._token or self._read_cache()
            if tok and tok.expires_at - self._clock() > REFRESH_MARGIN_S:
                self._token = tok
                return tok
        tok = self._fetch()
        self._token = tok
        self._write_cache(tok)
        return tok

    def _fetch(self) -> Token:
        resp = self._http.post(
            f"{self._base_url}{TOKEN_PATH}",
            headers={"Authorization": f"ApiKey {self._key}", "Accept": "*/*"},
        )
        if resp.status_code != 200:
            raise GarminApiError(resp.status_code, "POST", TOKEN_PATH)
        access = resp.text.strip()
        return Token(access_token=access, expires_at=jwt_expiry(access))

    def _read_cache(self) -> Token | None:
        try:
            data = json.loads(self._cache_path.read_text())
            if data["fingerprint"] != self._fingerprint:
                return None
            return Token(access_token=data["access_token"], expires_at=float(data["expires_at"]))
        except (FileNotFoundError, KeyError, TypeError, ValueError):
            return None

    def _write_cache(self, tok: Token) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._cache_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(
                {
                    "fingerprint": self._fingerprint,
                    "access_token": tok.access_token,
                    "expires_at": tok.expires_at,
                },
                f,
            )
