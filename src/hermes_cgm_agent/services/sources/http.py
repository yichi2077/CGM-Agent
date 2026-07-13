from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from hermes_cgm_agent.services.sources.models import SourceKind


class SourceHTTPError(RuntimeError):
    """Safe, credential-free error from an Android/Nightscout bridge."""


@dataclass(frozen=True)
class HTTPSourceClient:
    timeout_seconds: float = 10.0
    retry_attempts: int = 3
    retry_backoff_seconds: float = 1.0
    api_secret: str | None = None
    access_token: str | None = None
    max_response_bytes: int = 2 * 1024 * 1024
    sleep: Callable[[float], None] = time.sleep

    def fetch_json(self, *, url: str, kind: SourceKind, count: int) -> tuple[str, Any]:
        base_request_url = build_source_url(url=url, kind=kind, count=count)
        validate_source_url(base_request_url)
        request_url = _with_access_token(base_request_url, self.access_token)
        public_url = redact_source_url(base_request_url)
        headers = {"Accept": "application/json", "User-Agent": "hermes-cgm-agent/0.1"}
        if self.api_secret:
            # Nightscout and Juggluco both accept the SHA-1 api-secret header.
            # Sending the digest avoids putting the plaintext secret in URLs,
            # process arguments, import batches, audit logs, and exceptions.
            headers["api-secret"] = _api_secret_digest(self.api_secret)
        request = urllib.request.Request(request_url, headers=headers)

        attempts = max(1, int(self.retry_attempts))
        for attempt in range(1, attempts + 1):
            try:
                with urllib.request.urlopen(  # noqa: S310
                    request, timeout=max(0.1, float(self.timeout_seconds))
                ) as response:
                    payload = response.read(self.max_response_bytes + 1)
                if len(payload) > self.max_response_bytes:
                    raise SourceHTTPError(f"CGM bridge response exceeds {self.max_response_bytes} bytes")
                try:
                    return public_url, json.loads(payload.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise SourceHTTPError("CGM bridge returned invalid JSON") from exc
            except urllib.error.HTTPError as exc:
                if exc.code not in {408, 425, 429, 500, 502, 503, 504}:
                    raise SourceHTTPError(f"CGM bridge HTTP request failed with status {exc.code}") from exc
                last_error: Exception = exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc

            if attempt < attempts:
                self.sleep(max(0.0, self.retry_backoff_seconds) * (2 ** (attempt - 1)))

        reason = getattr(last_error, "reason", None) or str(last_error)
        raise SourceHTTPError(f"CGM bridge request failed after {attempts} attempts: {reason}") from last_error


def build_source_url(*, url: str, kind: SourceKind, count: int) -> str:
    if count < 1 or count > 1000:
        raise ValueError("count must be between 1 and 1000")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("source URL must use http or https")
    path = parsed.path or "/"
    if path == "/":
        if kind in {"xdrip", "juggluco"}:
            path = "/sgv.json"
        else:
            path = "/api/v1/entries/sgv.json"
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    if not any(key == "count" for key, _ in query):
        query.append(("count", str(count)))
    return urllib.parse.urlunparse(parsed._replace(path=path, query=urllib.parse.urlencode(query)))


def validate_source_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "https":
        return
    if parsed.scheme != "http":
        raise ValueError("source URL must use http or https")
    if _allow_insecure_http():
        return
    host = parsed.hostname
    if host and _is_private_or_local(host):
        return
    raise ValueError(
        "HTTP source URLs must be localhost/private by default; use HTTPS or set "
        "CGM_SOURCE_ALLOW_INSECURE_HTTP=true for an explicit test override."
    )


def _with_access_token(url: str, token: str | None) -> str:
    if not token:
        return url
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    if not any(key == "token" for key, _ in query):
        query.append(("token", token))
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query)))


def redact_source_url(url: str) -> str:
    """Remove bridge credentials from a URL before persistence or display."""

    parsed = urllib.parse.urlparse(url)
    query = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in {"token", "api_secret", "api-secret"}
    ]
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query)))


def _api_secret_digest(secret: str) -> str:
    normalized = secret.strip()
    if len(normalized) == 40 and all(char in "0123456789abcdefABCDEF" for char in normalized):
        return normalized.lower()
    return hashlib.sha1(normalized.encode("utf-8"), usedforsecurity=False).hexdigest()


def _allow_insecure_http() -> bool:
    return os.environ.get("CGM_SOURCE_ALLOW_INSECURE_HTTP", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _is_private_or_local(host: str) -> bool:
    normalized = host.strip().strip("[]").lower()
    if normalized in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        ip = ipaddress.ip_address(normalized)
    except ValueError:
        return normalized.endswith(".local")
    return ip.is_private or ip.is_loopback or ip.is_link_local
