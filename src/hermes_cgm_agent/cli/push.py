from __future__ import annotations

import json
from pathlib import Path

from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.scheduling import (
    PushSchedulerConfig,
    PushSchedulerService,
)
from hermes_cgm_agent.storage.sqlite import SQLiteStore

from hermes_cgm_agent.cli.utils import _parse_iso_datetime


def _push_tick(
    *,
    db_path: Path,
    user_id: str,
    now: str | None,
    timezone_name: str,
) -> int:
    store = SQLiteStore(db_path)
    store.initialize()
    service = PushSchedulerService(
        store=store,
        config=PushSchedulerConfig(timezone=timezone_name),
        audit_service=AuditService(store),
    )
    result = service.push_tick(
        user_id=user_id,
        now=_parse_iso_datetime(now) if now else None,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0
