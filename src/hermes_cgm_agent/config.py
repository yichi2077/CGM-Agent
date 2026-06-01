from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNTIME_DIR = PROJECT_ROOT / ".runtime"
DEFAULT_HERMES_EXE = Path(
    r"C:\Users\postgres\AppData\Local\hermes\hermes-agent\venv\Scripts\hermes.exe"
)
DEFAULT_DB_PATH = DEFAULT_RUNTIME_DIR / "app.db"


@dataclass(frozen=True)
class AppConfig:
    hermes_bin: str | None = None
    default_model: str | None = None
    default_provider: str | None = None
    default_toolsets: str | None = None
    default_skills: str | None = None
    timeout_seconds: int = 300
    db_path: str = str(DEFAULT_DB_PATH)
    host: str = "127.0.0.1"
    port: int = 8000

    @classmethod
    def from_env(cls) -> "AppConfig":
        timeout_raw = os.getenv("CGM_AGENT_TIMEOUT_SECONDS", "300")
        try:
            timeout_seconds = int(timeout_raw)
        except ValueError:
            timeout_seconds = 300

        port_raw = os.getenv("CGM_AGENT_PORT", "8000")
        try:
            port = int(port_raw)
        except ValueError:
            port = 8000

        return cls(
            hermes_bin=os.getenv("HERMES_BIN"),
            default_model=os.getenv("CGM_AGENT_MODEL"),
            default_provider=os.getenv("CGM_AGENT_PROVIDER"),
            default_toolsets=os.getenv("CGM_AGENT_TOOLSETS"),
            default_skills=os.getenv("CGM_AGENT_SKILLS"),
            timeout_seconds=timeout_seconds,
            db_path=os.getenv("CGM_AGENT_DB_PATH", str(DEFAULT_DB_PATH)),
            host=os.getenv("CGM_AGENT_HOST", "127.0.0.1"),
            port=port,
        )

    @property
    def database_path(self) -> Path:
        return Path(self.db_path)

    @property
    def runtime_dir(self) -> Path:
        return self.database_path.parent
