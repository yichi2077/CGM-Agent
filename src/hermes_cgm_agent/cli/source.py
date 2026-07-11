from __future__ import annotations

import json
from pathlib import Path

from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.sources import SourcePollConfig, SourcePollService
from hermes_cgm_agent.storage.sqlite import SQLiteStore


def _source_poll(
    *,
    db_path: Path,
    user_id: str,
    kind: str,
    url: str,
    count: int,
    source: str | None,
    expected_interval_minutes: int,
) -> int:
    store = SQLiteStore(db_path)
    store.initialize()
    service = SourcePollService(
        repository=SQLiteCGMRepository(store),
        config=SourcePollConfig(expected_interval_minutes=expected_interval_minutes),
    )
    try:
        result = service.poll(
            user_id=user_id,
            kind=kind,
            url=url,
            count=count,
            source=source,
        )
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 1
    body = result.to_dict()
    body["database_path"] = str(db_path)
    print(json.dumps(body, ensure_ascii=False, sort_keys=True))
    return 0
