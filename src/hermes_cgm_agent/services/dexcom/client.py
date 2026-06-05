from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Deque

from hermes_cgm_agent.services.dexcom.config import DexcomConfig

# Dexcom v3 query parameters use naive ISO-8601 with second precision and no
# timezone suffix; the service interprets them against the UTC systemTime axis.
DEXCOM_QUERY_DT_FORMAT = "%Y-%m-%dT%H:%M:%S"


class DexcomError(Exception):
    """Base class for all Dexcom integration failures."""


class DexcomAuthError(DexcomError):
    """OAuth failure: bad/expired credentials, missing token, or HTTP 401.

    Carries the OAuth ``error`` code (e.g. ``invalid_grant``) when available so
    the caller can distinguish "re-run dexcom-auth" from "bad client secret".
    """

    def __init__(self, message: str, *, oauth_error: str | None = None) -> None:
        super().__init__(message)
        self.oauth_error = oauth_error


class DexcomRateLimitError(DexcomError):
    """HTTP 429 from the Dexcom API; ``retry_after`` is seconds when provided."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class DexcomAPIError(DexcomError):
    """Any other non-success HTTP status or transport-level failure."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class HTTPResult:
    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)


# A transport is any callable that performs one HTTP request. The default uses
# the standard library; tests inject a fake to run the client fully offline.
Transport = Callable[[urllib.request.Request, float], HTTPResult]


def _default_transport(request: urllib.request.Request, timeout: float) -> HTTPResult:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (fixed Dexcom hosts)
            return HTTPResult(
                status=response.status,
                body=response.read(),
                headers={k.lower(): v for k, v in response.headers.items()},
            )
    except urllib.error.HTTPError as exc:  # 4xx/5xx still carry a body
        body = exc.read() if hasattr(exc, "read") else b""
        return HTTPResult(
            status=exc.code,
            body=body or b"",
            headers={k.lower(): v for k, v in (exc.headers or {}).items()},
        )
    except urllib.error.URLError as exc:  # DNS/connection/TLS failure
        raise DexcomAPIError(f"Dexcom request failed: {exc.reason}") from exc


class RateLimiter:
    """Sliding-window limiter capping requests per rolling 60-second window.

    Dexcom throttles the public app tier at roughly 20 requests/minute. The
    ``monotonic`` and ``sleep`` seams keep the limiter fully deterministic under
    test (no real wall-clock waiting)."""

    def __init__(
        self,
        max_per_minute: int,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.max_per_minute = max(1, int(max_per_minute))
        self._monotonic = monotonic
        self._sleep = sleep
        self._calls: Deque[float] = deque()

    def acquire(self) -> None:
        now = self._monotonic()
        self._evict(now)
        if len(self._calls) >= self.max_per_minute:
            wait = 60.0 - (now - self._calls[0])
            if wait > 0:
                self._sleep(wait)
                now = self._monotonic()
                self._evict(now)
        self._calls.append(self._monotonic())

    def _evict(self, now: float) -> None:
        while self._calls and now - self._calls[0] >= 60.0:
            self._calls.popleft()


@dataclass(frozen=True)
class TokenResponse:
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str = "Bearer"
    scope: str | None = None
    obtained_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def expires_at(self) -> datetime:
        return self.obtained_at + timedelta(seconds=self.expires_in)

    @classmethod
    def from_payload(cls, payload: dict[str, Any], *, obtained_at: datetime | None = None) -> "TokenResponse":
        try:
            access_token = str(payload["access_token"])
            refresh_token = str(payload["refresh_token"])
            expires_in = int(payload["expires_in"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DexcomAuthError(f"Malformed Dexcom token response: {payload}") from exc
        return cls(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in,
            token_type=str(payload.get("token_type", "Bearer")),
            scope=payload.get("scope"),
            obtained_at=obtained_at or datetime.now(timezone.utc),
        )


def _to_query_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        aware = value.replace(tzinfo=timezone.utc)
    else:
        aware = value.astimezone(timezone.utc)
    return aware.strftime(DEXCOM_QUERY_DT_FORMAT)


class DexcomClient:
    """Thin Dexcom API v3 client over ``urllib`` (no third-party HTTP deps).

    Token persistence and auto-refresh live in :class:`DexcomAuthService`; this
    client only performs individual authenticated requests and OAuth exchanges.
    """

    def __init__(
        self,
        config: DexcomConfig,
        *,
        rate_limiter: RateLimiter | None = None,
        transport: Transport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.config = config
        self._transport = transport or _default_transport
        self._timeout = timeout
        self._rate_limiter = rate_limiter or RateLimiter(config.max_requests_per_minute)

    # -- OAuth ---------------------------------------------------------------

    def build_authorize_url(self, *, state: str | None = None) -> str:
        params = {
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "response_type": "code",
            "scope": self.config.scope,
        }
        if state:
            params["state"] = state
        return f"{self.config.base_url}/v3/oauth2/login?{urllib.parse.urlencode(params)}"

    def exchange_code(self, code: str) -> TokenResponse:
        return self._post_token(
            {
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": self.config.redirect_uri,
            }
        )

    def refresh_token(self, refresh_token: str) -> TokenResponse:
        return self._post_token(
            {
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
                "redirect_uri": self.config.redirect_uri,
            }
        )

    # -- Data ----------------------------------------------------------------

    def get_data_range(self, access_token: str) -> dict[str, Any]:
        return self._get("/v3/users/self/dataRange", access_token=access_token)

    def get_egvs(self, access_token: str, *, start: datetime, end: datetime) -> dict[str, Any]:
        return self._get(
            "/v3/users/self/egvs",
            access_token=access_token,
            params={"startDate": _to_query_datetime(start), "endDate": _to_query_datetime(end)},
        )

    def get_events(self, access_token: str, *, start: datetime, end: datetime) -> dict[str, Any]:
        return self._get(
            "/v3/users/self/events",
            access_token=access_token,
            params={"startDate": _to_query_datetime(start), "endDate": _to_query_datetime(end)},
        )

    # -- internals -----------------------------------------------------------

    def _post_token(self, data: dict[str, str]) -> TokenResponse:
        request = urllib.request.Request(
            url=f"{self.config.base_url}/v3/oauth2/token",
            data=urllib.parse.urlencode(data).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        )
        self._rate_limiter.acquire()
        result = self._transport(request, self._timeout)
        if result.status == 200:
            return TokenResponse.from_payload(_parse_json(result))
        payload = _safe_json(result.body)
        oauth_error = payload.get("error") if isinstance(payload, dict) else None
        message = f"Dexcom token request failed (HTTP {result.status})"
        if oauth_error:
            message = f"{message}: {oauth_error}"
        # invalid_client => bad client_id/secret; invalid_grant => stale code/refresh.
        raise DexcomAuthError(message, oauth_error=oauth_error)

    def _get(
        self,
        path: str,
        *,
        access_token: str,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.config.base_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(
            url=url,
            method="GET",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        )
        self._rate_limiter.acquire()
        result = self._transport(request, self._timeout)
        if result.status == 200:
            return _parse_json(result)
        if result.status == 401:
            raise DexcomAuthError("Dexcom access token rejected (HTTP 401)")
        if result.status == 429:
            raise DexcomRateLimitError(
                "Dexcom rate limit exceeded (HTTP 429)",
                retry_after=_parse_retry_after(result.headers.get("retry-after")),
            )
        raise DexcomAPIError(
            f"Dexcom request to {path} failed (HTTP {result.status}): "
            f"{result.body.decode('utf-8', 'replace')[:300]}",
            status_code=result.status,
        )


def _parse_json(result: HTTPResult) -> dict[str, Any]:
    try:
        payload = json.loads(result.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise DexcomAPIError("Dexcom returned a non-JSON response", status_code=result.status) from exc
    if not isinstance(payload, dict):
        raise DexcomAPIError("Dexcom returned an unexpected JSON shape", status_code=result.status)
    return payload


def _safe_json(body: bytes) -> Any:
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None
