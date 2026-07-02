from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from hermes_cgm_agent.domain import ImportIssue, RawCGMRecord, RawImportBatch
from hermes_cgm_agent.services.data import (
    CGMNormalizer,
    NormalizationConfig,
    SQLiteCGMRepository,
)


@dataclass(frozen=True)
class StreamIngestResult:
    inserted: int = 0
    duplicate: int = 0
    issues: int = 0


class StreamIngestor:
    def __init__(
        self,
        *,
        repository: SQLiteCGMRepository,
        user_id: str,
        source: str,
        default_timezone: str = "UTC",
        normalizer: CGMNormalizer | None = None,
    ) -> None:
        self.repository = repository
        self.user_id = user_id
        self.source = source
        self.default_timezone = default_timezone
        self.normalizer = normalizer or CGMNormalizer()
        self.inserted = 0
        self.duplicate = 0
        self.issues = 0
        self._batch_archived = False

    def archive_batch(self, batch: RawImportBatch) -> None:
        if self._batch_archived:
            return
        self.repository.create_import_batch(batch)
        self.issues += len(batch.issues)
        self._batch_archived = True

    def ingest_record(self, record: RawCGMRecord, *, batch_id: str) -> StreamIngestResult:
        batch = RawImportBatch(
            batch_id=batch_id,
            source_name="simulation-stream",
            source_format=record.source_format,
            records=[record],
            issues=[],
        )
        normalized = self.normalizer.normalize_batch(
            batch,
            NormalizationConfig(
                user_id=self.user_id,
                source=self.source,
                default_timezone=self.default_timezone,
            ),
        )
        inserted = 0
        duplicate = normalized.duplicate_count
        issues = len(normalized.issues)
        for point in normalized.points:
            try:
                self.repository.create_glucose_point(point)
                inserted += 1
            except sqlite3.IntegrityError:
                duplicate += 1
        self.inserted += inserted
        self.duplicate += duplicate
        self.issues += issues
        return StreamIngestResult(inserted=inserted, duplicate=duplicate, issues=issues)

    def totals(self) -> StreamIngestResult:
        return StreamIngestResult(
            inserted=self.inserted,
            duplicate=self.duplicate,
            issues=self.issues,
        )


def issue_to_dict(issue: ImportIssue) -> dict:
    return issue.model_dump(mode="json")
