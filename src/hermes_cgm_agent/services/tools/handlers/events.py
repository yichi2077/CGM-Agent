from __future__ import annotations

import uuid
from typing import Any

from pydantic import ValidationError

from hermes_cgm_agent.domain import UserEvent
from hermes_cgm_agent.services.data import EventToolService
from hermes_cgm_agent.services.tools.handlers.base import (
    BaseToolHandler,
    ToolExecutionResponse,
    describe_argument_error,
)
from hermes_cgm_agent.services.tools.handlers.helpers import event_evidence


class EventHandlerMixin(BaseToolHandler):
    def _create_event(
        self,
        *,
        arguments: dict[str, Any],
        session_id: str,
    ) -> ToolExecutionResponse:
        spec = self.registry.get("events.create")
        try:
            user_id = str(arguments["user_id"])
            event_raw = arguments.get("event")
            if not isinstance(event_raw, dict):
                raise ValueError("event must be an object")
            event_raw = dict(event_raw)
            # Tolerant argument shape (D054): real LLMs attach free text under
            # ad-hoc keys (note/description/...). Instead of rejecting the whole
            # event for an unknown key, fold unknowns into `payload` — the
            # documented freeform-details field. The security fields below are
            # still force-overwritten and can never ride in through this path.
            known = {"type", "event_type", "ts_start", "ts_end", "payload",
                     "confidence", "attachment", "is_sensitive"}
            # L-13: for meal events, keep food_items/meal_time at top level so
            # _apply_meal_structure can process them. For non-meal events they
            # stay as extras and get folded into payload.
            event_type = event_raw.get("event_type", event_raw.get("type"))
            if event_type == "meal":
                known = known | {"food_items", "meal_time"}
            extras = {k: event_raw.pop(k) for k in list(event_raw) if k not in known}
            if extras:
                payload = event_raw.get("payload")
                merged = dict(payload) if isinstance(payload, dict) else {}
                merged.update(extras)
                event_raw["payload"] = merged
            # F1: structured meal fields — move top-level food_items/meal_time
            # into payload and generate a structured Chinese summary so the
            # event carries both the raw structured data and a human-readable
            # digest (used by reports, memory recall, and BM25 search).
            if event_type == "meal":
                _apply_meal_structure(event_raw)
            # Force technical/provenance fields server-side (D045 / FR-007, Damocles W2):
            # the model supplies only event_type + ts_start (+ optional ts_end/payload/
            # confidence). The id, owner, provenance and confirmation flag are NOT
            # model-controllable, so an agent-created event can never masquerade as a
            # user-authored or user-confirmed fact.
            event_raw["event_id"] = uuid.uuid4().hex
            event_raw["user_id"] = user_id
            event_raw["created_by"] = "agent"
            event_raw["user_confirmed"] = False
            # L-17: record the optional reason parameter in the audit trail.
            reason = arguments.get("reason")
            event = UserEvent.model_validate(event_raw)
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            return self._error_response(
                session_id=session_id,
                tool_name=spec.name,
                risk_level=spec.risk_level,
                data_scope={"user_id": arguments.get("user_id")},
                message=describe_argument_error(exc),
            )

        event_id = self.repository.create_user_event(event)
        saved = self.repository.get_user_event(event_id, include_rejected=True)
        evidence_refs = [event_evidence(saved, action="created")]
        audit_id = self.audit_service.log(
            session_id=session_id,
            event_type="tool_call",
            payload={
                "tool_name": spec.name,
                "status": "ok",
                "data_scope": {"user_id": saved.user_id},
                "risk_level": spec.risk_level,
                "evidence_refs": evidence_refs,
                "event_id": saved.event_id,
                "user_confirmed": saved.user_confirmed,
                "is_rejected": saved.is_rejected,
                "reason": reason,  # L-17: persist reason for audit trail
            },
        )
        return ToolExecutionResponse(
            status="ok",
            evidence_refs=evidence_refs,
            audit_id=audit_id,
            payload={
                "event_id": saved.event_id,
                "event": saved.model_dump(mode="json", by_alias=True),
            },
        )

    def _confirm_event(
        self,
        *,
        arguments: dict[str, Any],
        session_id: str,
    ) -> ToolExecutionResponse:
        spec = self.registry.get("events.confirm")
        try:
            user_id = str(arguments["user_id"])
            event_id = str(arguments["event_id"])
            result = EventToolService(self.repository).confirm_event(arguments)
            saved = result.event
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            return self._error_response(
                session_id=session_id,
                tool_name=spec.name,
                risk_level=spec.risk_level,
                data_scope={
                    "user_id": arguments.get("user_id"),
                    "event_id": arguments.get("event_id"),
                },
                message=describe_argument_error(exc),
            )

        evidence_refs = [
            event_evidence(saved, action="confirmed" if result.confirmed else "rejected")
        ]
        audit_id = self.audit_service.log(
            session_id=session_id,
            event_type="tool_call",
            payload={
                "tool_name": spec.name,
                "status": "ok",
                "data_scope": {"user_id": saved.user_id, "event_id": saved.event_id},
                "risk_level": spec.risk_level,
                "evidence_refs": evidence_refs,
                "event_id": saved.event_id,
                "confirmed": result.confirmed,
                "user_confirmed": saved.user_confirmed,
                "is_rejected": saved.is_rejected,
            },
        )
        return ToolExecutionResponse(
            status="ok",
            evidence_refs=evidence_refs,
            audit_id=audit_id,
            payload={
                "event_id": saved.event_id,
                "event": saved.model_dump(mode="json", by_alias=True),
            },
        )


# ------------------------------------------------------------------
# F1: structured meal field helpers (module-level)
# ------------------------------------------------------------------

_MEAL_TIME_CN: dict[str, str] = {
    "breakfast": "早餐",
    "lunch": "午餐",
    "dinner": "晚餐",
    "snack": "加餐",
}


def _apply_meal_structure(event_raw: dict[str, Any]) -> None:
    """Move top-level ``food_items`` / ``meal_time`` into ``payload`` and
    generate a ``structured_summary`` (F1).

    Works in-place on the event dict that will be passed to
    ``UserEvent.model_validate``.  Handles three input shapes:

    1. Fields sent as top-level event properties (the schema-advertised path).
    2. Fields already inside ``payload`` (a model that nests them).
    3. Fields absent (a free-text meal event — nothing to do).
    """
    food_items = event_raw.pop("food_items", None)
    meal_time = event_raw.pop("meal_time", None)

    # M-17: validate food_items before storing into payload — must be a
    # list of dicts, each with a "name" key (matches the tool schema).
    if food_items is not None:
        _validate_food_items(food_items)

    payload = event_raw.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    else:
        payload = dict(payload)  # copy so we don't mutate the original

    if food_items is not None:
        payload["food_items"] = food_items
    if meal_time is not None:
        payload["meal_time"] = meal_time

    # Generate structured summary from whatever is now in payload.
    fi = payload.get("food_items")
    mt = payload.get("meal_time")
    if fi or mt:
        summary = _build_meal_summary(fi, mt)
        if summary:
            payload["structured_summary"] = summary

    if payload:
        event_raw["payload"] = payload


def _build_meal_summary(
    food_items: Any | None,
    meal_time: str | None,
) -> str:
    """Generate a structured Chinese summary for a meal event (F1).

    Examples:
        >>> _build_meal_summary([{"name": "面条", "portion": "一碗"}], "lunch")
        '午餐：面条（一碗）'
        >>> _build_meal_summary(
        ...     [{"name": "面条", "portion": "一碗", "estimated_carbs_g": 60},
        ...      {"name": "水果", "portion": "一份", "estimated_carbs_g": 15}],
        ...     "lunch")
        '午餐：面条（一碗）、水果（一份）。预估碳水约75g'
    """
    time_label = _MEAL_TIME_CN.get(meal_time, "") if meal_time else ""

    item_strs: list[str] = []
    total_carbs: float = 0.0
    has_carbs = False
    if isinstance(food_items, list):
        for item in food_items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            portion = str(item.get("portion") or "").strip()
            s = f"{name}（{portion}）" if portion else name
            item_strs.append(s)
            carbs = item.get("estimated_carbs_g")
            if isinstance(carbs, (int, float)) and not isinstance(carbs, bool) and carbs >= 0:
                total_carbs += carbs
                has_carbs = True

    food_text = "、".join(item_strs)
    prefix = f"{time_label}：" if (time_label and food_text) else time_label
    carbs_text = f"。预估碳水约{total_carbs:.0f}g" if (has_carbs and total_carbs > 0) else ""

    return f"{prefix}{food_text}{carbs_text}"


def _validate_food_items(food_items: Any) -> None:
    """M-17: validate that food_items is a list of dicts, each with a ``name`` key.

    Matches the tool schema: ``food_items`` is an array of objects where each
    object MUST have a ``name`` string.  Raises ``ValueError`` on any mismatch
    so the handler's argument-error path surfaces an actionable message.
    """
    if not isinstance(food_items, list):
        raise ValueError("food_items must be a list")
    for idx, item in enumerate(food_items):
        if not isinstance(item, dict):
            raise ValueError(f"food_items[{idx}] must be an object")
        if not isinstance(item.get("name"), str) or not item["name"].strip():
            raise ValueError(f"food_items[{idx}] must have a non-empty 'name' string")
