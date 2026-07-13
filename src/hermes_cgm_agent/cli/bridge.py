from __future__ import annotations

import json
from pathlib import Path

from hermes_cgm_agent.config import default_hermes_home
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.sources import (
    BridgeConfig,
    SourcePollConfig,
    SourcePollService,
    check_bridge_health,
)
from hermes_cgm_agent.storage.sqlite import SQLiteStore


def _bridge_status(*, db_path: Path) -> int:
    try:
        config = BridgeConfig.from_env()
    except ValueError as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": str(exc),
                    "database_path": str(db_path),
                    "cron_script_installed": (default_hermes_home() / "scripts" / "cgm_bridge_poll.py").is_file(),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1
    try:
        result = check_bridge_health(config)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "kind": config.kind,
                    "url": config.url,
                    "source": config.source,
                    "error": str(exc),
                    "database_path": str(db_path),
                    "cron_script_installed": (default_hermes_home() / "scripts" / "cgm_bridge_poll.py").is_file(),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1
    body = result.to_dict()
    body["user_id"] = config.user_id
    body["database_path"] = str(db_path)
    body["cron_script_installed"] = (default_hermes_home() / "scripts" / "cgm_bridge_poll.py").is_file()
    print(json.dumps(body, ensure_ascii=False, sort_keys=True))
    return 0 if result.status == "ready" else 2


def _bridge_poll(*, db_path: Path, session_kind: str = "cli") -> int:
    store = SQLiteStore(db_path)
    store.initialize()
    try:
        config = BridgeConfig.from_env()
        result = SourcePollService(
            repository=SQLiteCGMRepository(store),
            client=config.build_client(),
            config=SourcePollConfig(
                expected_interval_minutes=config.expected_interval_minutes,
                max_stale_minutes=config.max_stale_minutes,
            ),
        ).poll(
            user_id=config.user_id,
            kind=config.kind,
            url=config.url,
            count=config.count,
            source=config.source,
        )
    except Exception as exc:
        user_id = _configured_user_id()
        store.create_audit_log(
            session_id=f"bridge-{session_kind}:{user_id}",
            event_type="bridge_poll_failed",
            payload={"user_id": user_id, "error": str(exc)},
        )
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 1

    degraded = result.stale or result.future_clock_skew or result.parsed_count == 0
    body = result.to_dict()
    body["status"] = "degraded" if degraded else "ok"
    body["database_path"] = str(db_path)
    store.create_audit_log(
        session_id=f"bridge-{session_kind}:{config.user_id}",
        event_type="bridge_poll_degraded" if degraded else "bridge_poll_completed",
        payload=body,
    )
    print(json.dumps(body, ensure_ascii=False, sort_keys=True))
    return 2 if degraded else 0


def _configured_user_id() -> str:
    import os

    return (os.getenv("CGM_AGENT_USER_ID") or "unconfigured").strip()
