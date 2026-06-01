from __future__ import annotations

from hermes_cgm_agent.services.memory.repository import (
    MEMORY_TABLES,
    SQLiteMemoryRepository,
    new_id,
)
from hermes_cgm_agent.services.memory.assembler import MemoryContextAssembler
from hermes_cgm_agent.services.memory.consolidation import (
    ConsolidationConfig,
    ConsolidationReport,
    ConsolidationService,
)
from hermes_cgm_agent.services.memory.provider import CGMMemoryProvider
from hermes_cgm_agent.services.memory.review import (
    IngestResult,
    MemoryReviewService,
)
from hermes_cgm_agent.services.memory.retrieval import (
    BM25Index,
    Embedder,
    HashingEmbedder,
    HybridRetriever,
    MemoryDoc,
    RetrievalResult,
)

__all__ = [
    "MEMORY_TABLES",
    "SQLiteMemoryRepository",
    "new_id",
    "CGMMemoryProvider",
    "ConsolidationConfig",
    "ConsolidationReport",
    "ConsolidationService",
    "IngestResult",
    "MemoryContextAssembler",
    "MemoryReviewService",
    "BM25Index",
    "Embedder",
    "HashingEmbedder",
    "HybridRetriever",
    "MemoryDoc",
    "RetrievalResult",
]
