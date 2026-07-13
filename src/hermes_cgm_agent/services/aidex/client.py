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

from hermes_cgm_agent.services.aidex.config import AidexConfig


AIDEX_QUERY_DT_FORMAT = "%Y-%m-%dT%H:%M:%S"


class AidexError(Exception):
    """Base error for the official MicroTech LinX/AiDEX API."""


class AidexAuthError(AidexError):
    def __init__(self, message: str, *, oauth_error: str | None = None) -> None:
        super().__init__(message)
        self.oauth_error = oauth_error


class AidexRateLimitError(AidexError):
    pass


class AidexAPIError(AidexError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class HTTPResult:
    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)


Transport = Callable[[urllib.request.Request, float], HTTPResult]


def _default_transport(request: urllib.request.Request, timeout: float) -> HTTPResult:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return HTTPResult(
                status=response.status,
                body=response.read(),
                headers={key.lower(): value for key, value in response.headers.items()},
            )
    except urllib.error.HTTPError as exc:
        return HTTPResult(
            status=exc.code,
            body=exc.read() if hasattr(exc, "read") else b"",
            headers={key.lower(): value for key, value in (exc.headers or {}).items()},
        )
    except urllib.error.URLError as exc:
        raise AidexAPIError(f"AiDEX request failed: {exc.reason}") from exc


class RateLimiter:
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
        while self._calls and now - self._calls[0] >= 60:
            self._calls.popleft()
        if len(self._calls) >= self.max_per_minute:
            wait = 60 - (now - self._calls[0])
            if wait > 0:
                self._sleep(wait)
        self._calls.append(self._monotonic())


@dataclass(frozen=True)
class AidexTokenResponse:
    access_token: str
    refresh_token: str
    expires_in: int
    obtained_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def expires_at(self) -> datetime:
        return self.obtained_at + timedelta(seconds=self.expires_in)

    @classmethod
    def from_payload(
        cls, payload: dict[str, Any], *, obtained_at: datetime | None = None
    ) -> "AidexTokenResponse":
        try:
            return cls(
                access_token=str(payload["accessToken"]),
                refresh_token=str(payload["refreshToken"]),
                expires_in=int(payload["expiresIn"]),
                obtained_at=obtained_at or datetime.now(timezone.utc),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AidexAuthError("Malformed AiDEX token response") from exc


def _query_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime(AIDEX_QUERY_DT_FORMAT)


class AidexClient:
    """Official LinX/AiDEX OAuth and sensor-glucose API client."""

    def __init__(
        self,
        config: AidexConfig,
        *,
        transport: Transport | None = None,
        rate_limiter: RateLimiter | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.config = config
        self._transport = transport or _default_transport
        self._rate_limiter = rate_limiter or RateLimiter(config.max_requests_per_minute)
        self._timeout = timeout

    def build_authorize_url(self, *, state: str | None = None) -> str:
        query = {"clientId": self.config.client_id, "responseType": "code"}
        if state:
            query["state"] = state
        return f"{self.config.base_url}/v1/oauth2/authorize?{urllib.parse.urlencode(query)}"

    def exchange_code(self, code: str) -> AidexTokenResponse:
        payload = self._request_json(
            method="POST",
            path="/v1/oauth2/token",
            form={
                "clientId": self.config.client_id,
                "clientSecret": self.config.client_secret,
                "code": code,
                "grantType": "authorization_code",
            },
        )
        return AidexTokenResponse.from_payload(payload)

    def refresh_token(self, refresh_token: str) -> AidexTokenResponse:
        payload = self._request_json(
            method="POST",
            path="/v1/oauth2/token",
            form={"refreshToken": refresh_token, "grantType": "refresh_token"},
        )
        return AidexTokenResponse.from_payload(payload)

    def get_data_range(self, access_token: str) -> dict[str, Any]:
        return self._request_json(
            method="GET", path="/v1/user/public/data-range", access_token=access_token
        )

    def get_sensor_glucose(
        self, access_token: str, *, start: datetime, end: datetime
    ) -> dict[str, Any]:
        query = urllib.parse.urlencode(
            {"startTime": _query_datetime(start), "endTime": _query_datetime(end)}
        )
        return self._request_json(
            method="GET",
            path=f"/v1/user/glu/sensor-glucose?{query}",
            access_token=access_token,
        )

    def _request_json(
        self,
        *,
        method: str,
        path: str,
        form: dict[str, str] | None = None,
        access_token: str | None = None,
    ) -> dict[str, Any]:
        self._rate_limiter.acquire()
        data = urllib.parse.urlencode(form).encode("utf-8") if form is not None else None
        headers = {"Accept": "application/json", "User-Agent": "hermes-cgm-agent/aidex"}
        if form is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if access_token:
            # The official contract specifies the raw access token in this
            # header, not an RFC 6750 `Bearer` prefix.
            headers["authorization"] = access_token
        request = urllib.request.Request(
            f"{self.config.base_url}{path}", data=data, headers=headers, method=method
        )
        result = self._transport(request, self._timeout)
        try:
            payload = json.loads(result.body.decode("utf-8")) if result.body else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AidexAPIError(
                "AiDEX API returned non-JSON data", status_code=result.status
            ) from exc
        if not isinstance(payload, dict):
            raise AidexAPIError("AiDEX API returned a non-object JSON response")
        self._raise_for_error(result.status, payload)
        return payload

    @staticmethod
    def _raise_for_error(status: int, payload: dict[str, Any]) -> None:
        error = str(payload.get("error") or "").strip()
        code = payload.get("code")
        message = str(payload.get("msg") or error or f"HTTP {status}")
        if status in {401, 403} or code == 10003 or error:
            raise AidexAuthError(f"AiDEX authorization failed: {message}", oauth_error=error or None)
        if status == 429 or code == 10010:
            raise AidexRateLimitError(f"AiDEX rate limit exceeded: {message}")
        if status < 200 or status >= 300 or (code is not None and code != 1):
            raise AidexAPIError(f"AiDEX API error: {message}", status_code=status)
