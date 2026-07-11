from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from hermes_cgm_agent.services.data import (
    CGMImporter,
    CGMNormalizer,
    NormalizationConfig,
    SQLiteCGMRepository,
)
from hermes_cgm_agent.storage.sqlite import SQLiteStore


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
