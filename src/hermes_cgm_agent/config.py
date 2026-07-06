from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNTIME_DIR = PROJECT_ROOT / ".runtime"
# Legacy standalone store. Kept as the migration source only; runtime defaults
# must resolve through the Hermes profile path so CLI and Hermes tools share data.
DEFAULT_DB_PATH = DEFAULT_RUNTIME_DIR / "app.db"
DEFAULT_STORAGE_KEY_PATH = DEFAULT_RUNTIME_DIR / "storage.key"


def _candidate_hermes_paths() -> list[Path]:
    home = Path.home()
    if sys.platform.startswith("win"):
        local_appdata = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        return [
            local_appdata / "hermes" / "hermes-agent" / "venv" / "Scripts" / "hermes.exe",
            home / ".hermes" / "bin" / "hermes.exe",
        ]
    return [
        home / ".hermes" / "bin" / "hermes",
        home / ".local" / "bin" / "hermes",
        Path("/usr/local/bin/hermes"),
        Path("/opt/homebrew/bin/hermes"),
    ]


def default_hermes_exe() -> Path | None:
    for candidate in _candidate_hermes_paths():
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue
    return None


DEFAULT_HERMES_EXE = default_hermes_exe()


DEFAULT_USER_ID_ENV = "CGM_AGENT_USER_ID"
DISPLAY_UNIT_ENV = "CGM_AGENT_DISPLAY_UNIT"


def display_glucose_unit() -> str:
    """User-facing glucose unit preference (D052).

    Every mature CGM product (xDrip+, Nightscout, vendor apps) treats display
    units as a basic setting; Chinese AiDEX users think in mmol/L. Internal
    storage/analytics stay mg/dL — this only affects rendered text. Accepted
    values: ``mg/dL`` (default) or ``mmol/L`` (common spellings tolerated).
    """
    raw = os.getenv(DISPLAY_UNIT_ENV, "").strip().lower().replace(" ", "")
    if raw in {"mmol/l", "mmoll", "mmol"}:
        return "mmol/L"
    return "mg/dL"


def default_user_id() -> str:
    """Single-user identity for a personal deployment (P0-1 / D052).

    Every entry point that needs a user id when the caller did not supply one
    — the memory provider, tool calls whose arguments omit ``user_id``, and
    CLI defaults — resolves through here, so CGM data, memories, and tool
    reads can never split across accidentally different ids.
    """
    return os.getenv(DEFAULT_USER_ID_ENV, "").strip() or "demo-user"


def default_hermes_home() -> Path:
    env = os.getenv("HERMES_HOME")
    if env:
        return Path(env).expanduser().resolve()
    home = Path.home()
    if sys.platform.startswith("win"):
        local_appdata = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        return (local_appdata / "hermes").resolve()
    return (home / ".hermes").resolve()


def resolve_database_path(hermes_home: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the CGM SQLite path shared by every Hermes integration entry point.

    Both the standalone capability-tool plugin (``cgm``) and the memory-provider
    plugin (``cgm_memory``) must agree on a single database file, otherwise tools
    write glucose/events/reports to one DB while the memory layer reads from
    another (split-brain — see NEW-1). This is the single source of truth.

    Precedence:
      1. ``CGM_AGENT_DB_PATH`` env var: explicit operator override.
      2. ``<hermes_home>/cgm-agent/app.db``: explicit Hermes profile path.
      3. ``default_hermes_home()/cgm-agent/app.db``: platform Hermes profile.
    """
    env_db = os.getenv("CGM_AGENT_DB_PATH")
    if env_db:
        return Path(env_db).expanduser().resolve()
    home = str(hermes_home or "").strip()
    if not home:
        home = str(default_hermes_home())
    if home:
        return (Path(home).expanduser() / "cgm-agent" / "app.db").resolve()
    return Path(DEFAULT_DB_PATH)


@dataclass(frozen=True)
class AppConfig:
    hermes_bin: str | None = None
    default_model: str | None = None
    default_provider: str | None = None
    default_toolsets: str | None = None
    default_skills: str | None = None
    timeout_seconds: int = 300
    db_path: str = str(DEFAULT_DB_PATH)
    storage_key_path: str = str(DEFAULT_STORAGE_KEY_PATH)

    @classmethod
    def from_env(cls) -> "AppConfig":
        timeout_raw = os.getenv("CGM_AGENT_TIMEOUT_SECONDS", "300")
        try:
            timeout_seconds = int(timeout_raw)
        except ValueError:
            timeout_seconds = 300

        # Route the CLI entry point through the SAME resolver the cgm/cgm_memory
        # plugins use (D045 / F1 A1). Previously this hardcoded DEFAULT_DB_PATH, so
        # the CLI wrote .runtime/app.db while the agent read ~/.hermes/cgm-agent/app.db
        # — a split-brain store the user could never see in Hermes.
        db = resolve_database_path()

        # The Fernet key MUST live beside its database (SQLiteStore default), so a
        # correctly located store is always decryptable. An explicit override is
        # honored but warned about when it separates the key from the DB.
        storage_key = os.getenv("CGM_AGENT_STORAGE_KEY_PATH", str(db.parent / "storage.key"))
        if Path(storage_key).expanduser().resolve().parent != db.parent:
            logging.getLogger("hermes_cgm_agent.config").warning(
                "storage_key_path (%s) is not in the database directory (%s); "
                "the Fernet key may be separated from its database.",
                storage_key,
                db.parent,
            )

        return cls(
            hermes_bin=os.getenv("HERMES_BIN"),
            default_model=os.getenv("CGM_AGENT_MODEL"),
            default_provider=os.getenv("CGM_AGENT_PROVIDER"),
            default_toolsets=os.getenv("CGM_AGENT_TOOLSETS"),
            default_skills=os.getenv("CGM_AGENT_SKILLS"),
            timeout_seconds=timeout_seconds,
            db_path=str(db),
            storage_key_path=storage_key,
        )

    @property
    def database_path(self) -> Path:
        return Path(self.db_path)

    @property
    def runtime_dir(self) -> Path:
        return self.database_path.parent

    @property
    def resolved_storage_key_path(self) -> Path:
        return Path(self.storage_key_path)
