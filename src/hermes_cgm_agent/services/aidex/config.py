from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values

from hermes_cgm_agent.config import default_hermes_home


SANDBOX_BASE_URL = "https://sandbox-accesslist-x.microtechmd.com"
PRODUCTION_BASE_URL = "https://accesslist-x.microtechmd.com"
AIDEX_ENV_NAMES = frozenset(
    {
        "AIDEX_CLIENT_ID",
        "AIDEX_CLIENT_SECRET",
        "AIDEX_USE_SANDBOX",
        "AIDEX_MAX_REQUESTS_PER_MINUTE",
        "AIDEX_BASE_URL",
        "AIDEX_SYNC_OVERLAP_MINUTES",
        "AIDEX_SYNC_BOOTSTRAP_HOURS",
        "CGM_AGENT_DB_PATH",
        "CGM_AGENT_STORAGE_KEY_PATH",
        "CGM_AGENT_USER_ID",
    }
)


def load_aidex_environment(*, hermes_home: Path | None = None) -> Path | None:
    """Load only AiDEX/CGM settings from the active Hermes ``.env``.

    Hermes loads this file for the gateway, but the project support CLI runs
    in a separate process. Restricting the imported names keeps unrelated LLM
    and messaging credentials out of the project process. Explicit process
    variables always win over persisted values.
    """

    env_path = (hermes_home or default_hermes_home()) / ".env"
    if not env_path.is_file():
        return None
    values = dotenv_values(env_path, encoding="utf-8")
    for name in AIDEX_ENV_NAMES:
        value = values.get(name)
        if value is not None and name not in os.environ:
            os.environ[name] = value
    return env_path


def aidex_cron_user_id() -> str:
    """Return the explicitly configured single-user identity for automation."""

    user_id = (os.getenv("CGM_AGENT_USER_ID") or "").strip()
    if not user_id:
        raise ValueError(
            "CGM_AGENT_USER_ID must be set before enabling AiDEX cron sync; "
            "the demo-user fallback is not safe for real CGM data."
        )
    return user_id


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AidexConfig:
    """MicroTech LinX/AiDEX Open API connection settings.

    The official platform issues a client id/secret per registered application.
    Production access additionally requires the application to be approved for
    authentic-user data; sandbox access uses the same OAuth 2.0 flow.
    """

    client_id: str
    client_secret: str
    use_sandbox: bool = True
    max_requests_per_minute: int = 120
    base_url_override: str | None = None

    @property
    def base_url(self) -> str:
        if self.base_url_override:
            return self.base_url_override.rstrip("/")
        return SANDBOX_BASE_URL if self.use_sandbox else PRODUCTION_BASE_URL

    @property
    def environment(self) -> str:
        return "sandbox" if self.use_sandbox else "production"

    @property
    def source_label(self) -> str:
        return f"aidex:{self.environment}"

    @classmethod
    def from_env(cls) -> "AidexConfig":
        client_id = (os.getenv("AIDEX_CLIENT_ID") or "").strip()
        client_secret = (os.getenv("AIDEX_CLIENT_SECRET") or "").strip()
        if not client_id or not client_secret:
            raise ValueError(
                "AiDEX API credentials are not configured. Register an app at the "
                "MicroTech LinX API Open Platform, then set AIDEX_CLIENT_ID and "
                "AIDEX_CLIENT_SECRET."
            )

        base_url_override = (os.getenv("AIDEX_BASE_URL") or "").strip() or None
        if base_url_override:
            parsed = urlparse(base_url_override)
            if parsed.scheme != "https" or not parsed.netloc:
                raise ValueError("AIDEX_BASE_URL must be an absolute HTTPS URL")

        try:
            max_rpm = int(os.getenv("AIDEX_MAX_REQUESTS_PER_MINUTE", "120"))
        except ValueError:
            max_rpm = 120
        return cls(
            client_id=client_id,
            client_secret=client_secret,
            use_sandbox=_env_bool("AIDEX_USE_SANDBOX", True),
            max_requests_per_minute=max(1, min(max_rpm, 1000)),
            base_url_override=base_url_override,
        )
