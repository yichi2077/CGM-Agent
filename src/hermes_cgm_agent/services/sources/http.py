from __future__ import annotations

import ipaddress
import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from hermes_cgm_agent.services.sources.models import SourceKind


@dataclass(frozen=True)
class HTTPSourceClient:
    timeout_seconds: float = 10.0

    def fetch_json(self, *, url: str, kind: SourceKind, count: int) -> tuple[str, Any]:
        request_url = build_source_url(url=url, kind=kind, count=count)
        validate_source_url(request_url)
        request = urllib.request.Request(
            request_url,
            headers={"Accept": "application/json", "User-Agent": "hermes-cgm-agent/0.1"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
            payload = response.read()
        return request_url, json.loads(payload.decode("utf-8"))


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
    return urllib.parse.urlunparse(
        parsed._replace(path=path, query=urllib.parse.urlencode(query))
    )


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
