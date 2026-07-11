from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from hermes_cgm_agent.config import AppConfig, default_hermes_exe


def _warn_legacy_store_if_relevant(config: AppConfig) -> None:
    """Hint the user to migrate when legacy ``.runtime`` data exists but the active
    canonical store has not been created yet (F1 / D045 W4).

    Printed to stderr so command stdout stays machine-parseable; suppressed once the
    canonical store exists or when the active path coincides with the legacy path
    (e.g. standalone dev runs with no HERMES_HOME).
    """
    from hermes_cgm_agent.config import DEFAULT_DB_PATH

    legacy = Path(DEFAULT_DB_PATH)
    target = config.database_path
    try:
        same = target.resolve() == legacy.resolve()
    except OSError:
        same = str(target) == str(legacy)
    if legacy.exists() and not same and not target.exists():
        print(
            f"[cgm-agent] legacy data detected at {legacy}, but the active store "
            f"({target}) is empty. Run `python -m hermes_cgm_agent migrate-db` to "
            "move your data + key to the canonical path.",
            file=sys.stderr,
        )


def _hermes_status(config: AppConfig) -> dict[str, object]:
    hermes_bin = _resolve_hermes_bin(config.hermes_bin)
    try:
        completed = subprocess.run(
            [hermes_bin, "--version"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "available": False,
            "executable": hermes_bin,
            "version": None,
            "detail": str(exc),
        }
    output = (completed.stdout or completed.stderr).strip()
    return {
        "available": completed.returncode == 0,
        "executable": hermes_bin,
        "version": output.splitlines()[0] if output else None,
        "detail": output or None,
    }


def _resolve_hermes_bin(configured: str | None) -> str:
    if configured:
        return configured
    discovered = shutil.which("hermes")
    if discovered:
        return discovered
    fallback = default_hermes_exe()
    return str(fallback) if fallback is not None else "hermes"
