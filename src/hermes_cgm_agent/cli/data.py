from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.data import (
    CGMImporter,
    CGMNormalizer,
    NormalizationConfig,
    SQLiteCGMRepository,
)
from hermes_cgm_agent.services.tools import ToolExecutor
from hermes_cgm_agent.storage.sqlite import SQLiteStore

from hermes_cgm_agent.cli.utils import _read_json_object


def _import_cgm(
    *,
    db_path: Path,
    file_path: Path,
    source_format: str,
    user_id: str,
    timezone_name: str,
    source: str | None,
) -> int:
    store = SQLiteStore(db_path)
    store.initialize()
    repository = SQLiteCGMRepository(store)
    importer = CGMImporter()

    if source_format == "csv":
        batch = importer.import_csv(file_path)
    elif source_format == "json":
        batch = importer.import_json(file_path)
    else:
        raise ValueError(f"Unsupported import format: {source_format}")

    normalizer = CGMNormalizer()
    normalized = normalizer.normalize_batch(
        batch,
        NormalizationConfig(
            user_id=user_id,
            source=source or f"{source_format}:{file_path.stem}",
            default_timezone=timezone_name,
        ),
    )
    stored_batch = batch.model_copy(
        update={"issues": [*batch.issues, *normalized.issues]}
    )
    repository.create_import_batch(stored_batch)

    inserted_count = 0
    duplicate_count = 0
    for point in normalized.points:
        try:
            repository.create_glucose_point(point)
            inserted_count += 1
        except sqlite3.IntegrityError:
            duplicate_count += 1

    payload = {
        "status": "ok",
        "batch_id": stored_batch.batch_id,
        "source_name": stored_batch.source_name,
        "source_format": stored_batch.source_format,
        "raw_record_count": stored_batch.record_count,
        "import_issue_count": stored_batch.issue_count,
        "normalized_point_count": len(normalized.points),
        "inserted_point_count": inserted_count,
        "duplicate_point_count": duplicate_count + normalized.duplicate_count,
        "missing_range_count": len(normalized.missing_ranges),
        "database_path": str(db_path),
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def _tool_call(
    *,
    db_path: Path,
    tool_name: str,
    input_path: Path,
    session_id: str,
) -> int:
    store = SQLiteStore(db_path)
    store.initialize()
    arguments = _read_json_object(input_path)
    executor = ToolExecutor(
        repository=SQLiteCGMRepository(store),
        audit_service=AuditService(store),
    )
    response = executor.execute(
        tool_name=tool_name,
        arguments=arguments,
        session_id=session_id,
    )
    body = response.to_dict()
    print(json.dumps(body, ensure_ascii=False, sort_keys=True))
    from hermes_cgm_agent.services.tools.handlers.base import FAILURE_STATUSES

    return 1 if response.status in FAILURE_STATUSES else 0
