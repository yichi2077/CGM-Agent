from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

from hermes_cgm_agent.config import default_hermes_home
from hermes_cgm_agent.services.sources.http import HTTPSourceClient, validate_source_url
from hermes_cgm_agent.services.sources.models import SourceKind


BRIDGE_ENV_NAMES = frozenset(
    {
        "CGM_AGENT_DB_PATH",
        "CGM_AGENT_STORAGE_KEY_PATH",
        "CGM_AGENT_USER_ID",
        "CGM_BRIDGE_KIND",
        "CGM_BRIDGE_URL",
        "CGM_BRIDGE_API_SECRET",
        "CGM_BRIDGE_ACCESS_TOKEN",
        "CGM_BRIDGE_ALLOW_UNAUTHENTICATED",
        "CGM_BRIDGE_SOURCE",
        "CGM_BRIDGE_COUNT",
        "CGM_BRIDGE_EXPECTED_INTERVAL_MINUTES",
        "CGM_BRIDGE_MAX_STALE_MINUTES",
        "CGM_BRIDGE_TIMEOUT_SECONDS",
        "CGM_BRIDGE_RETRY_ATTEMPTS",
        "CGM_BRIDGE_RETRY_BACKOFF_SECONDS",
    }
)


def load_bridge_environment(*, hermes_home: Path | None = None) -> Path | None:
    """Load only Android bridge and shared CGM settings from Hermes ``.env``."""

    env_path = (hermes_home or default_hermes_home()) / ".env"
    if not env_path.is_file():
        return None
    values = dotenv_values(env_path, encoding="utf-8")
    for name in BRIDGE_ENV_NAMES:
        value = values.get(name)
        if value is not None and name not in os.environ:
            os.environ[name] = value
    return env_path


@dataclass(frozen=True)
class BridgeConfig:
    user_id: str
    kind: SourceKind
    url: str
    source: str
    count: int = 48
    expected_interval_minutes: int = 5
    max_stale_minutes: int = 12
    timeout_seconds: float = 10.0
    retry_attempts: int = 3
    retry_backoff_seconds: float = 1.0
    api_secret: str | None = None
    access_token: str | None = None

    @classmethod
    def from_env(cls) -> "BridgeConfig":
        user_id = (os.getenv("CGM_AGENT_USER_ID") or "").strip()
        if not user_id:
            raise ValueError(
                "CGM_AGENT_USER_ID must be set for the Android CGM bridge; "
                "the demo-user fallback is not safe for real data."
            )
        raw_kind = (os.getenv("CGM_BRIDGE_KIND") or "juggluco").strip().lower()
        if raw_kind not in {"xdrip", "juggluco", "nightscout"}:
            raise ValueError("CGM_BRIDGE_KIND must be xdrip, juggluco, or nightscout")
        kind: SourceKind = raw_kind  # type: ignore[assignment]
        url = (os.getenv("CGM_BRIDGE_URL") or "").strip()
        if not url:
            raise ValueError("CGM_BRIDGE_URL must point to the Android phone or Nightscout site")
        validate_source_url(url)

        count = _env_int("CGM_BRIDGE_COUNT", 48, minimum=1, maximum=1000)
        expected = _env_int("CGM_BRIDGE_EXPECTED_INTERVAL_MINUTES", 5, minimum=1, maximum=60)
        max_stale = _env_int("CGM_BRIDGE_MAX_STALE_MINUTES", 12, minimum=1, maximum=1440)
        retries = _env_int("CGM_BRIDGE_RETRY_ATTEMPTS", 3, minimum=1, maximum=10)
        timeout = _env_float("CGM_BRIDGE_TIMEOUT_SECONDS", 10.0, minimum=0.1, maximum=120)
        backoff = _env_float("CGM_BRIDGE_RETRY_BACKOFF_SECONDS", 1.0, minimum=0, maximum=60)
        source = (os.getenv("CGM_BRIDGE_SOURCE") or f"android:{kind}").strip()
        api_secret = (os.getenv("CGM_BRIDGE_API_SECRET") or "").strip() or None
        access_token = (os.getenv("CGM_BRIDGE_ACCESS_TOKEN") or "").strip() or None
        allow_unauthenticated = (os.getenv("CGM_BRIDGE_ALLOW_UNAUTHENTICATED") or "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        if not (api_secret or access_token or allow_unauthenticated):
            raise ValueError(
                "Configure CGM_BRIDGE_API_SECRET or CGM_BRIDGE_ACCESS_TOKEN; "
                "set CGM_BRIDGE_ALLOW_UNAUTHENTICATED=true only for an isolated test feed."
            )
        return cls(
            user_id=user_id,
            kind=kind,
            url=url,
            source=source,
            count=count,
            expected_interval_minutes=expected,
            max_stale_minutes=max_stale,
            timeout_seconds=timeout,
            retry_attempts=retries,
            retry_backoff_seconds=backoff,
            api_secret=api_secret,
            access_token=access_token,
        )

    def build_client(self) -> HTTPSourceClient:
        return HTTPSourceClient(
            timeout_seconds=self.timeout_seconds,
            retry_attempts=self.retry_attempts,
            retry_backoff_seconds=self.retry_backoff_seconds,
            api_secret=self.api_secret,
            access_token=self.access_token,
        )


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = (os.getenv(name) or str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = (os.getenv(name) or str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value
