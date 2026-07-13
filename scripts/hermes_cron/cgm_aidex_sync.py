"""Hermes no-agent cron entry for incremental official AiDEX API sync."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _project_root() -> Path:
    configured = os.getenv("CGM_AGENT_PROJECT_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    hermes_home = Path(os.getenv("HERMES_HOME") or Path.home() / ".hermes")
    marker = hermes_home / "cgm-agent-project-root.txt"
    if marker.exists():
        return Path(marker.read_text(encoding="utf-8").strip()).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


root = _project_root()
src = root / "src"
if str(src) not in sys.path:
    sys.path.insert(0, str(src))

from hermes_cgm_agent.config import AppConfig  # noqa: E402
from hermes_cgm_agent.services.aidex import (  # noqa: E402
    AidexError,
    aidex_cron_user_id,
    build_aidex_sync_service,
    load_aidex_environment,
)
from hermes_cgm_agent.services.data import SQLiteCGMRepository  # noqa: E402
from hermes_cgm_agent.storage.sqlite import SQLiteStore  # noqa: E402


def main() -> int:
    load_aidex_environment()
    config = AppConfig.from_env()
    store = SQLiteStore(config.database_path)
    store.initialize()
    try:
        user_id = aidex_cron_user_id()
        result = build_aidex_sync_service(SQLiteCGMRepository(store)).sync(
            user_id=user_id,
            incremental=True,
            overlap_minutes=int(os.getenv("AIDEX_SYNC_OVERLAP_MINUTES", "15")),
            bootstrap_hours=int(os.getenv("AIDEX_SYNC_BOOTSTRAP_HOURS", "24")),
        )
    except (AidexError, ValueError) as exc:
        audit_user_id = (os.getenv("CGM_AGENT_USER_ID") or "unconfigured").strip()
        store.create_audit_log(
            session_id=f"aidex-cron:{audit_user_id}",
            event_type="aidex_sync_failed",
            payload={"user_id": audit_user_id, "error": str(exc)},
        )
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 1
    store.create_audit_log(
        session_id=f"aidex-cron:{user_id}",
        event_type="aidex_sync_completed",
        payload=result.to_dict(),
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
