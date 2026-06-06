from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hermes_cgm_agent.domain import L2ProfileItem
from hermes_cgm_agent.services.memory.repository import SQLiteMemoryRepository

CGM_USER_MD_START = "<!-- CGM_AGENT_L2_PROFILE_START -->"
CGM_USER_MD_END = "<!-- CGM_AGENT_L2_PROFILE_END -->"


@dataclass(frozen=True)
class UserMDSyncResult:
    user_md_path: str
    item_count: int
    wrote: bool


class UserMDSyncService:
    """One-way L2 profile export into Hermes USER.md managed block (D039)."""

    def __init__(self, *, repository: SQLiteMemoryRepository) -> None:
        self.repository = repository

    def sync(self, *, user_id: str, hermes_home: str | Path) -> UserMDSyncResult:
        home = Path(hermes_home).expanduser().resolve()
        home.mkdir(parents=True, exist_ok=True)
        path = home / "USER.md"
        items = self.repository.list_profile_items(user_id)
        block = render_l2_user_md_block(items)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        updated = replace_managed_block(existing, block)
        if updated != existing:
            path.write_text(updated, encoding="utf-8")
            wrote = True
        else:
            wrote = False
        return UserMDSyncResult(
            user_md_path=str(path),
            item_count=len(items),
            wrote=wrote,
        )


def render_l2_user_md_block(items: list[L2ProfileItem]) -> str:
    lines = [
        CGM_USER_MD_START,
        "# CGM Profile Memory",
        "",
        "This block is managed by hermes-cgm-agent. Edit CGM preferences through memory tools.",
        "",
    ]
    if not items:
        lines.append("- No active CGM profile items yet.")
    for item in items:
        summary = _profile_summary(item)
        lines.append(
            f"- `{item.key}` ({item.confidence:.2f}, evidence={item.evidence_count}): {summary}"
        )
    lines.extend(["", CGM_USER_MD_END, ""])
    return "\n".join(lines)


def replace_managed_block(existing: str, block: str) -> str:
    if CGM_USER_MD_START in existing and CGM_USER_MD_END in existing:
        before, rest = existing.split(CGM_USER_MD_START, 1)
        _, after = rest.split(CGM_USER_MD_END, 1)
        return before.rstrip() + "\n\n" + block + after.lstrip()
    if existing.strip():
        return existing.rstrip() + "\n\n" + block
    return block


def _profile_summary(item: L2ProfileItem) -> str:
    value: dict[str, Any] = item.value or {}
    for key in ("summary", "statement", "text", "description"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    if value:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return item.key
