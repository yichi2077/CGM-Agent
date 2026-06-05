from __future__ import annotations

import os
from dataclasses import dataclass

# Dexcom API v3 hosts. Verified against developer.dexcom.com/docs/dexcom/
# authentication: both OAuth2 endpoints (login + token) and the data endpoints
# live under ``/v3/`` for the v3 API.
#   - authorize/login: GET  {base}/v3/oauth2/login
#   - token/refresh:   POST {base}/v3/oauth2/token  (x-www-form-urlencoded)
#   - data:            GET  {base}/v3/users/self/{egvs,events,dataRange}
#
# Dexcom runs region-segregated stacks: a developer app AND the user's data
# account both live in exactly one region, and they must match (a US-portal app
# cannot read OUS/EU data, and vice versa). ``region`` selects the host family.
_REGION_HOSTS: dict[str, tuple[str, str]] = {
    # region: (production_base, sandbox_base)
    "us": ("https://api.dexcom.com", "https://sandbox-api.dexcom.com"),
    "ous": ("https://api.dexcom.eu", "https://sandbox-api.dexcom.eu"),
    "jp": ("https://api.dexcom.jp", "https://sandbox-api.dexcom.jp"),
}

# Aliases accepted from DEXCOM_REGION so callers can say "eu"/"au"/"australia".
_REGION_ALIASES: dict[str, str] = {
    "us": "us", "usa": "us", "america": "us",
    "ous": "ous", "eu": "ous", "europe": "ous", "au": "ous", "australia": "ous",
    "row": "ous", "world": "ous", "intl": "ous", "international": "ous",
    "jp": "jp", "japan": "jp",
}

DEFAULT_REGION = "us"

SANDBOX_BASE_URL = _REGION_HOSTS[DEFAULT_REGION][1]
PRODUCTION_BASE_URL = _REGION_HOSTS[DEFAULT_REGION][0]


def normalize_region(value: str | None) -> str:
    key = (value or DEFAULT_REGION).strip().lower()
    return _REGION_ALIASES.get(key, DEFAULT_REGION)

# Default OAuth scope. ``offline_access`` is required to receive a refresh_token
# so the access_token can be refreshed transparently after it expires.
DEFAULT_SCOPE = "offline_access"

# Dexcom's documented placeholder redirect URI for testing. Must match a value
# registered on the developer app AND the one used in the authorize request.
DEFAULT_REDIRECT_URI = "https://www.google.com"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class DexcomConfig:
    """Connection settings for the Dexcom API v3 client.

    Sourced from ``DEXCOM_CLIENT_ID`` / ``DEXCOM_CLIENT_SECRET`` /
    ``DEXCOM_REDIRECT_URI`` and the ``DEXCOM_USE_SANDBOX`` switch.
    """

    client_id: str
    client_secret: str
    redirect_uri: str = DEFAULT_REDIRECT_URI
    use_sandbox: bool = True
    scope: str = DEFAULT_SCOPE
    # Region of the Dexcom developer app + data account: "us", "ous" (EU/AU/RoW),
    # or "jp". Must match where the user's CGM data actually lives.
    region: str = DEFAULT_REGION
    # Maximum API requests allowed per rolling minute (Dexcom limits ~ 20 req/min
    # for the public app tier; kept conservative and configurable).
    max_requests_per_minute: int = 20
    # Explicit API host override (``DEXCOM_BASE_URL``). When set it wins over the
    # region/sandbox host table — used to point the real client + CLI at a local
    # mock Dexcom server for offline end-to-end testing. Empty/None = real hosts.
    base_url_override: str | None = None

    @property
    def base_url(self) -> str:
        if self.base_url_override:
            return self.base_url_override.rstrip("/")
        production, sandbox = _REGION_HOSTS.get(self.region, _REGION_HOSTS[DEFAULT_REGION])
        return sandbox if self.use_sandbox else production

    @property
    def environment(self) -> str:
        return "sandbox" if self.use_sandbox else "production"

    @property
    def source_label(self) -> str:
        """Stable ``source`` value stamped onto every synced GlucosePoint so that
        sandbox and production rows never collide on the UNIQUE(user, ts, source)
        dedup key."""
        return f"dexcom:{self.environment}"

    @classmethod
    def from_env(cls) -> "DexcomConfig":
        client_id = (os.getenv("DEXCOM_CLIENT_ID") or "").strip()
        client_secret = (os.getenv("DEXCOM_CLIENT_SECRET") or "").strip()
        if not client_id or not client_secret:
            raise ValueError(
                "Dexcom credentials are not configured. Set DEXCOM_CLIENT_ID and "
                "DEXCOM_CLIENT_SECRET (and optionally DEXCOM_REDIRECT_URI / "
                "DEXCOM_USE_SANDBOX) before running Dexcom commands."
            )
        redirect_uri = (os.getenv("DEXCOM_REDIRECT_URI") or DEFAULT_REDIRECT_URI).strip()
        max_rpm_raw = os.getenv("DEXCOM_MAX_REQUESTS_PER_MINUTE", "20")
        try:
            max_rpm = int(max_rpm_raw)
        except ValueError:
            max_rpm = 20
        return cls(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            use_sandbox=_env_bool("DEXCOM_USE_SANDBOX", True),
            scope=(os.getenv("DEXCOM_SCOPE") or DEFAULT_SCOPE).strip(),
            region=normalize_region(os.getenv("DEXCOM_REGION")),
            max_requests_per_minute=max(1, max_rpm),
            base_url_override=(os.getenv("DEXCOM_BASE_URL") or "").strip() or None,
        )
