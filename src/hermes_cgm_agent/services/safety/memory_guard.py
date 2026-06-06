"""One-way memory write protection + dual-track isolation (ADR-0001 §2.5 / D031).

Medical (``authoritative_kb``) and personal (``user_memory``) memory are
reverse-lifecycle and must never cross-contaminate:

- **Track isolation** — an authoritative context carries ONLY ``authoritative_kb``
  evidence; a user-memory context carries ONLY personal (``user_memory`` /
  ``memory``) evidence. A leak in either direction is a hard error.
- **One-way write protection** — personal memory can never be written into the
  medical KB. The KB is immutable packaged data with no runtime write API; this
  is asserted defensively so a future mutator can't be added silently.
- **Conflict resolution** — when a personal belief contradicts an authoritative
  fact, authoritative wins (D031). Downstream generation must present this
  gently, never as a denial of the user.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PERSONAL_KINDS = {"user_memory", "memory"}
AUTHORITATIVE_KINDS = {"authoritative_kb"}

CONFLICT_NOTE = "以权威医学证据为准,温和呈现,不否定用户既往记录。"


class MemoryTrackViolation(RuntimeError):
    """Raised when the medical and personal memory tracks cross-contaminate."""


def _kinds(refs: list[dict[str, Any]] | None) -> set[str]:
    return {str(ref.get("kind")) for ref in (refs or []) if isinstance(ref, dict)}


def assert_track_isolation(
    *,
    memory_items: list[dict[str, Any]] | None,
    authoritative_documents: list[dict[str, Any]] | None,
) -> None:
    """Fail loudly if either track carries the other track's evidence (D031)."""
    for item in memory_items or []:
        if _kinds(item.get("evidence_refs")) & AUTHORITATIVE_KINDS:
            raise MemoryTrackViolation(
                "authoritative_kb evidence leaked into the user_memory track"
            )
    for doc in authoritative_documents or []:
        if _kinds(doc.get("evidence_refs")) & PERSONAL_KINDS:
            raise MemoryTrackViolation(
                "user_memory evidence leaked into the authoritative_kb track"
            )


def assert_kb_readonly(rag_service: Any) -> None:
    """The medical KB must expose no mutation API (personal can never write it)."""
    for attr in ("add", "write", "insert", "upsert", "update", "delete", "save"):
        if hasattr(rag_service, attr):
            raise MemoryTrackViolation(
                f"authoritative KB must be read-only; found mutator '{attr}'"
            )


@dataclass(frozen=True)
class ConflictResolution:
    winner: str  # always "authoritative"
    authoritative: dict[str, Any]
    personal: dict[str, Any] | None
    note: str


def resolve_conflict(
    *,
    authoritative: dict[str, Any],
    personal: dict[str, Any] | None = None,
) -> ConflictResolution:
    """Medical always wins (D031); the note guides gentle downstream presentation."""
    return ConflictResolution(
        winner="authoritative",
        authoritative=authoritative,
        personal=personal,
        note=CONFLICT_NOTE,
    )
