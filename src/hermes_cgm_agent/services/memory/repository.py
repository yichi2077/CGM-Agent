"""SQLite memory repository for L1/L2/L3 + candidate queue (MEM-ARCH §5.1).

Self-built persistence + structured retrieval (time-window / type / state /
key filters). Hybrid (sparse+dense) semantic retrieval is layered on top in a
later unit (retrieval.py); this unit owns durable storage and the deterministic
structured filters that retrieval composes with.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from hermes_cgm_agent.domain import (
    CandidateStatus,
    EvidenceRef,
    HypothesisState,
    L1Episode,
    L2ProfileItem,
    L3Hypothesis,
    MemoryCandidate,
    MemoryLayer,
)
from hermes_cgm_agent.storage.sqlite import SQLiteStore

MEMORY_TABLES = [
    "l1_episodes",
    "l2_profile_items",
    "l3_hypotheses",
    "memory_candidates",
]


class SQLiteMemoryRepository:
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    # -- L1 episodes ---------------------------------------------------------

    def create_episode(self, episode: L1Episode) -> L1Episode:
        with self.store.connect() as conn:
            conn.execute(
                """
                INSERT INTO l1_episodes (
                    episode_id, user_id, occurred_at, episode_type, summary,
                    payload_json, evidence_refs_json, source_report_id,
                    source_section_id, confidence, is_archived, created_at,
                    last_referenced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    episode.episode_id,
                    episode.user_id,
                    _dt(episode.occurred_at),
                    episode.episode_type,
                    self.store.seal(episode.summary),
                    self.store.seal(episode.payload),
                    self.store.seal([ref.model_dump(mode="json") for ref in episode.evidence_refs]),
                    episode.source_report_id,
                    episode.source_section_id,
                    episode.confidence,
                    int(episode.is_archived),
                    _dt(episode.created_at),
                    _dt(episode.last_referenced_at),
                ),
            )
        return episode

    def get_episode(self, episode_id: str) -> L1Episode | None:
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT * FROM l1_episodes WHERE episode_id = ?", (episode_id,)
            ).fetchone()
        return _row_to_episode(row, self.store) if row else None

    def replace_episode(self, episode: L1Episode) -> L1Episode:
        with self.store.connect() as conn:
            conn.execute(
                """
                UPDATE l1_episodes SET
                    occurred_at = ?, episode_type = ?, summary = ?, payload_json = ?,
                    evidence_refs_json = ?, source_report_id = ?, source_section_id = ?,
                    confidence = ?, is_archived = ?, last_referenced_at = ?
                WHERE episode_id = ?
                """,
                (
                    _dt(episode.occurred_at),
                    episode.episode_type,
                    self.store.seal(episode.summary),
                    self.store.seal(episode.payload),
                    self.store.seal([ref.model_dump(mode="json") for ref in episode.evidence_refs]),
                    episode.source_report_id,
                    episode.source_section_id,
                    episode.confidence,
                    int(episode.is_archived),
                    _dt(episode.last_referenced_at),
                    episode.episode_id,
                ),
            )
        return episode

    def list_episodes(
        self,
        user_id: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        episode_type: str | None = None,
        include_archived: bool = False,
    ) -> list[L1Episode]:
        clauses = ["user_id = ?"]
        values: list[Any] = [user_id]
        if since is not None:
            clauses.append("occurred_at >= ?")
            values.append(_dt(since))
        if until is not None:
            clauses.append("occurred_at < ?")
            values.append(_dt(until))
        if episode_type is not None:
            clauses.append("episode_type = ?")
            values.append(episode_type)
        if not include_archived:
            clauses.append("is_archived = 0")
        sql = (
            "SELECT * FROM l1_episodes WHERE "
            + " AND ".join(clauses)
            + " ORDER BY occurred_at"
        )
        with self.store.connect() as conn:
            rows = conn.execute(sql, values).fetchall()
        return [_row_to_episode(row, self.store) for row in rows]

    def delete_episode(self, episode_id: str) -> bool:
        with self.store.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM l1_episodes WHERE episode_id = ?",
                (episode_id,),
            )
        return cursor.rowcount > 0

    def touch_episode(self, episode_id: str, *, when: datetime | None = None) -> None:
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE l1_episodes SET last_referenced_at = ? WHERE episode_id = ?",
                (_dt(when or _now()), episode_id),
            )

    def archive_stale_episodes(self, *, now: datetime | None = None, max_idle_days: int = 90) -> int:
        cutoff = (now or _now()) - timedelta(days=max_idle_days)
        with self.store.connect() as conn:
            cursor = conn.execute(
                "UPDATE l1_episodes SET is_archived = 1 WHERE is_archived = 0 AND last_referenced_at < ?",
                (_dt(cutoff),),
            )
            return cursor.rowcount

    # -- L2 profile items ----------------------------------------------------

    def upsert_profile_item(self, item: L2ProfileItem) -> L2ProfileItem:
        with self.store.connect() as conn:
            conn.execute(
                """
                INSERT INTO l2_profile_items (
                    item_id, user_id, key, value_json, confidence, evidence_count,
                    last_verified, supersedes_item_id, is_active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                    value_json = excluded.value_json,
                    confidence = excluded.confidence,
                    evidence_count = excluded.evidence_count,
                    last_verified = excluded.last_verified,
                    supersedes_item_id = excluded.supersedes_item_id,
                    is_active = excluded.is_active,
                    updated_at = excluded.updated_at
                """,
                (
                    item.item_id,
                    item.user_id,
                    item.key,
                    self.store.seal(item.value),
                    item.confidence,
                    item.evidence_count,
                    _dt(item.last_verified),
                    item.supersedes_item_id,
                    int(item.is_active),
                    _dt(item.created_at),
                    _dt(item.updated_at),
                ),
            )
        return item

    def list_profile_items(
        self,
        user_id: str,
        *,
        key: str | None = None,
        active_only: bool = True,
    ) -> list[L2ProfileItem]:
        clauses = ["user_id = ?"]
        values: list[Any] = [user_id]
        if key is not None:
            clauses.append("key = ?")
            values.append(key)
        if active_only:
            clauses.append("is_active = 1")
        sql = "SELECT * FROM l2_profile_items WHERE " + " AND ".join(clauses) + " ORDER BY key"
        with self.store.connect() as conn:
            rows = conn.execute(sql, values).fetchall()
        return [_row_to_profile(row, self.store) for row in rows]

    def delete_profile_item(self, item_id: str) -> bool:
        with self.store.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM l2_profile_items WHERE item_id = ?",
                (item_id,),
            )
        return cursor.rowcount > 0

    def decay_profile_items(
        self,
        *,
        now: datetime | None = None,
        stale_days: int = 30,
        decay: float = 0.2,
        deactivate_below: float = 0.3,
    ) -> int:
        """Periodic L2 decay: items not re-verified within stale_days lose
        confidence; below deactivate_below they stop being used (§5.1)."""
        cutoff = (now or _now()) - timedelta(days=stale_days)
        changed = 0
        with self.store.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM l2_profile_items WHERE is_active = 1 AND last_verified < ?",
                (_dt(cutoff),),
            ).fetchall()
            for row in rows:
                new_conf = max(0.0, round(row["confidence"] - decay, 4))
                is_active = 0 if new_conf < deactivate_below else 1
                conn.execute(
                    "UPDATE l2_profile_items SET confidence = ?, is_active = ?, updated_at = ? WHERE item_id = ?",
                    (new_conf, is_active, _dt(_now()), row["item_id"]),
                )
                changed += 1
        return changed

    # -- L3 hypotheses -------------------------------------------------------

    def upsert_hypothesis(self, hypothesis: L3Hypothesis) -> L3Hypothesis:
        with self.store.connect() as conn:
            conn.execute(
                """
                INSERT INTO l3_hypotheses (
                    hypothesis_id, user_id, statement, state, evidence_count,
                    contra_count, evidence_refs_json, last_checked, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(hypothesis_id) DO UPDATE SET
                    statement = excluded.statement,
                    state = excluded.state,
                    evidence_count = excluded.evidence_count,
                    contra_count = excluded.contra_count,
                    evidence_refs_json = excluded.evidence_refs_json,
                    last_checked = excluded.last_checked,
                    updated_at = excluded.updated_at
                """,
                (
                    hypothesis.hypothesis_id,
                    hypothesis.user_id,
                    self.store.seal(hypothesis.statement),
                    _enum(hypothesis.state),
                    hypothesis.evidence_count,
                    hypothesis.contra_count,
                    self.store.seal([ref.model_dump(mode="json") for ref in hypothesis.evidence_refs]),
                    _dt(hypothesis.last_checked),
                    _dt(hypothesis.created_at),
                    _dt(hypothesis.updated_at),
                ),
            )
        return hypothesis

    def list_hypotheses(
        self,
        user_id: str,
        *,
        states: list[HypothesisState] | None = None,
    ) -> list[L3Hypothesis]:
        clauses = ["user_id = ?"]
        values: list[Any] = [user_id]
        if states:
            expanded_states: list[str] = []
            for state in states:
                state_value = _enum(state)
                expanded_states.append(state_value)
                if state_value == HypothesisState.ARCHIVED.value:
                    # Backward-compatible read path for old rows persisted as
                    # `invalid` before the terminology change.
                    expanded_states.append("invalid")
            placeholders = ",".join("?" for _ in expanded_states)
            clauses.append(f"state IN ({placeholders})")
            values.extend(expanded_states)
        sql = "SELECT * FROM l3_hypotheses WHERE " + " AND ".join(clauses) + " ORDER BY updated_at DESC"
        with self.store.connect() as conn:
            rows = conn.execute(sql, values).fetchall()
        return [_row_to_hypothesis(row, self.store) for row in rows]

    def delete_hypothesis(self, hypothesis_id: str) -> bool:
        with self.store.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM l3_hypotheses WHERE hypothesis_id = ?",
                (hypothesis_id,),
            )
        return cursor.rowcount > 0

    # -- candidate queue -----------------------------------------------------

    def enqueue_candidate(self, candidate: MemoryCandidate) -> MemoryCandidate:
        with self.store.connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_candidates (
                    candidate_id, user_id, target_layer, candidate_type, summary,
                    requires_user_confirmation, status, source_report_id,
                    source_section_id, evidence_refs_json, confidence, created_at, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.candidate_id,
                    candidate.user_id,
                    _enum(candidate.target_layer),
                    candidate.candidate_type,
                    self.store.seal(candidate.summary),
                    int(candidate.requires_user_confirmation),
                    _enum(candidate.status),
                    candidate.source_report_id,
                    candidate.source_section_id,
                    self.store.seal([ref.model_dump(mode="json") for ref in candidate.evidence_refs]),
                    candidate.confidence,
                    _dt(candidate.created_at),
                    _dt(candidate.resolved_at) if candidate.resolved_at else None,
                ),
            )
        return candidate

    def list_candidates(
        self,
        user_id: str,
        *,
        status: CandidateStatus | None = None,
    ) -> list[MemoryCandidate]:
        clauses = ["user_id = ?"]
        values: list[Any] = [user_id]
        if status is not None:
            clauses.append("status = ?")
            values.append(_enum(status))
        sql = "SELECT * FROM memory_candidates WHERE " + " AND ".join(clauses) + " ORDER BY created_at"
        with self.store.connect() as conn:
            rows = conn.execute(sql, values).fetchall()
        return [_row_to_candidate(row, self.store) for row in rows]

    def set_candidate_status(
        self,
        candidate_id: str,
        *,
        status: CandidateStatus,
        when: datetime | None = None,
    ) -> MemoryCandidate:
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE memory_candidates SET status = ?, resolved_at = ? WHERE candidate_id = ?",
                (_enum(status), _dt(when or _now()), candidate_id),
            )
            row = conn.execute(
                "SELECT * FROM memory_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown candidate: {candidate_id}")
        return _row_to_candidate(row, self.store)


# -- helpers ----------------------------------------------------------------


def new_id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _dt(value: datetime) -> str:
    return value.isoformat()


def _enum(value: Any) -> str:
    """Return the enum's string value whether it's an Enum or already a str.

    Domain models use ``use_enum_values=True``, so fields may already hold the
    plain value; callers may also pass the Enum member directly.
    """
    return getattr(value, "value", value)


def _json(value: Any) -> str:
    return json.dumps(value, default=str)


def _refs(refs: list[EvidenceRef]) -> str:
    return json.dumps([ref.model_dump(mode="json") for ref in refs])


def _parse_refs(raw: object | None) -> list[EvidenceRef]:
    if not raw:
        return []
    if isinstance(raw, str):
        raw = json.loads(raw)
    return [EvidenceRef.model_validate(item) for item in raw]


def _row_to_episode(row: Any, store: SQLiteStore) -> L1Episode:
    return L1Episode(
        episode_id=row["episode_id"],
        user_id=row["user_id"],
        occurred_at=datetime.fromisoformat(row["occurred_at"]),
        episode_type=row["episode_type"],
        summary=store.unseal(row["summary"]),
        payload=store.unseal(row["payload_json"], legacy="json") or {},
        evidence_refs=_parse_refs(store.unseal(row["evidence_refs_json"], legacy="json")),
        source_report_id=row["source_report_id"],
        source_section_id=row["source_section_id"],
        confidence=row["confidence"],
        is_archived=bool(row["is_archived"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        last_referenced_at=datetime.fromisoformat(row["last_referenced_at"]),
    )


def _row_to_profile(row: Any, store: SQLiteStore) -> L2ProfileItem:
    return L2ProfileItem(
        item_id=row["item_id"],
        user_id=row["user_id"],
        key=row["key"],
        value=store.unseal(row["value_json"], legacy="json") or {},
        confidence=row["confidence"],
        evidence_count=row["evidence_count"],
        last_verified=datetime.fromisoformat(row["last_verified"]),
        supersedes_item_id=row["supersedes_item_id"],
        is_active=bool(row["is_active"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _row_to_hypothesis(row: Any, store: SQLiteStore) -> L3Hypothesis:
    state_value = row["state"]
    if state_value == "invalid":
        state_value = HypothesisState.ARCHIVED.value
    return L3Hypothesis(
        hypothesis_id=row["hypothesis_id"],
        user_id=row["user_id"],
        statement=store.unseal(row["statement"]),
        state=HypothesisState(state_value),
        evidence_count=row["evidence_count"],
        contra_count=row["contra_count"],
        evidence_refs=_parse_refs(store.unseal(row["evidence_refs_json"], legacy="json")),
        last_checked=datetime.fromisoformat(row["last_checked"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _row_to_candidate(row: Any, store: SQLiteStore) -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id=row["candidate_id"],
        user_id=row["user_id"],
        target_layer=MemoryLayer(row["target_layer"]),
        candidate_type=row["candidate_type"],
        summary=store.unseal(row["summary"]),
        requires_user_confirmation=bool(row["requires_user_confirmation"]),
        status=CandidateStatus(row["status"]),
        source_report_id=row["source_report_id"],
        source_section_id=row["source_section_id"],
        evidence_refs=_parse_refs(store.unseal(row["evidence_refs_json"], legacy="json")),
        confidence=row["confidence"],
        created_at=datetime.fromisoformat(row["created_at"]),
        resolved_at=datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None,
    )
