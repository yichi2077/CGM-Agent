from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from hermes_cgm_agent.services.arguments import optional_bool, optional_int
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.dexcom.sync import DexcomSyncResult, DexcomSyncService


DexcomSyncFactory = Callable[[SQLiteCGMRepository], DexcomSyncService]


@dataclass(frozen=True)
class DexcomSyncToolResult:
    user_id: str
    payload: dict[str, Any]


class DexcomSyncToolService:
    """Tool-facing orchestration for data.dexcom_sync."""

    def __init__(
        self,
        *,
        repository: SQLiteCGMRepository,
        sync_factory: DexcomSyncFactory,
    ) -> None:
        self.repository = repository
        self.sync_factory = sync_factory

    def sync(self, arguments: dict[str, Any]) -> DexcomSyncToolResult:
        user_id = str(arguments["user_id"])
        days = optional_int(
            arguments.get("days"),
            "days",
            default=7,
            minimum=1,
            maximum=90,
        )
        force = optional_bool(arguments.get("force"), "force", default=False)
        result: DexcomSyncResult = self.sync_factory(self.repository).sync(
            user_id=user_id,
            days=days,
            force=force,
        )
        return DexcomSyncToolResult(user_id=user_id, payload=result.to_dict())
