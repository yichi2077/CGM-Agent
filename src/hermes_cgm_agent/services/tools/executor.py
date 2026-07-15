from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.rag import AuthoritativeRAGToolService
from hermes_cgm_agent.services.tools.handlers import (
    ContextHandlerMixin,
    DeliveryHandlerMixin,
    EventHandlerMixin,
    MealCorrelationHandlerMixin,
    MemoryHandlerMixin,
    PushTickHandlerMixin,
    RagHandlerMixin,
    ReportHandlerMixin,
    TimeseriesHandlerMixin,
    ToolExecutionResponse,
)
from hermes_cgm_agent.services.tools.registry import ToolRegistry, build_default_tool_registry

# Re-exported for back-compat: callers import ToolExecutionResponse from this module.
__all__ = ["ToolExecutionResponse", "ToolExecutor"]


def _fill_default_user_id(arguments: dict[str, Any]) -> dict[str, Any]:
    """Inject the deployment's default user id where the caller omitted it.

    P0-1 (D052): in a real Hermes chat the model has no way to know the
    operator's user id — tool schemas require one, so the model INVENTED a
    string, silently splitting CGM data / memories / tool reads across ids.
    Explicit values always win; only missing/blank ids are filled, both at the
    top level and inside a ``data_scope`` object.
    """
    from hermes_cgm_agent.config import default_user_id

    filled = dict(arguments)
    enforce = os.getenv("CGM_AGENT_ENFORCE_USER_ID", "").strip() == "1"
    if enforce:
        # Hermes may supply its platform identity (usually ``default``) or a
        # model may hallucinate one. Acceptance/cutover profiles explicitly
        # opt into a single deployment identity so every tool read/write stays
        # on the same CGM user as the memory provider.
        filled["user_id"] = default_user_id()
    if not enforce and not str(filled.get("user_id") or "").strip():
        if "user_id" in filled or "data_scope" not in filled:
            filled["user_id"] = default_user_id()
    scope = filled.get("data_scope")
    if isinstance(scope, dict) and (enforce or not str(scope.get("user_id") or "").strip()):
        filled["data_scope"] = {**scope, "user_id": default_user_id()}
    return filled


def _apply_acceptance_time_anchor(arguments: dict[str, Any]) -> dict[str, Any]:
    """Shift model-supplied wall-clock windows onto the simulated DB anchor.

    Hermes itself has no virtual clock. During isolated acceptance the model
    naturally asks for "the last hour" using the host's current date, while
    the fixture ends several days earlier. The runner opts into this adapter
    with ``CGM_AGENT_ENFORCE_TIME_ANCHOR=1``; normal deployments are untouched.
    Durations and relative windows are preserved by applying one common UTC
    offset to recognised ISO datetime fields.
    """

    if os.getenv("CGM_AGENT_ENFORCE_TIME_ANCHOR", "").strip() != "1":
        return arguments
    raw_anchor = os.getenv("CGM_AGENT_ACCEPTANCE_ANCHOR_AT", "").strip()
    if not raw_anchor:
        return arguments
    try:
        anchor = datetime.fromisoformat(raw_anchor.replace("Z", "+00:00"))
        anchor = (anchor if anchor.tzinfo else anchor.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except ValueError:
        return arguments
    now = datetime.now(timezone.utc)
    delta = anchor - now
    timezone_name = os.getenv("CGM_AGENT_ACCEPTANCE_TIMEZONE", "UTC").strip() or "UTC"
    try:
        local_zone = ZoneInfo(timezone_name)
    except Exception:  # noqa: BLE001 - invalid optional env falls back safely
        local_zone = timezone.utc

    def shift(value: Any, *, force_anchor: bool = False) -> Any:
        if not isinstance(value, str) or not value.strip():
            return value
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
        parsed = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        shifted = anchor if force_anchor else parsed.astimezone(timezone.utc) + delta
        return shifted.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def parse_datetime(value: Any) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(
            timezone.utc
        )

    def iso(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def walk(value: Any, key: str = "", inherited_window_label: str = "") -> Any:
        if isinstance(value, dict):
            # Model-generated scopes are based on the host clock.  Shifting
            # each endpoint independently leaves the end a few hours away
            # from the simulated anchor when the model uses a local timezone.
            # Anchor the end explicitly and preserve the requested duration;
            # this keeps every CGM tool on one deterministic time slice.
            start = parse_datetime(value.get("window_start"))
            end = parse_datetime(value.get("window_end"))
            window_label = str(value.get("window_label") or inherited_window_label).lower()
            result = {
                child_key: walk(child, child_key, window_label)
                for child_key, child in value.items()
            }
            if start is not None and end is not None and end >= start:
                result["window_end"] = iso(anchor)
                result["window_start"] = iso(anchor - (end - start))
                if window_label in {"day", "month"}:
                    local_anchor = anchor.astimezone(local_zone)
                    if window_label == "day":
                        local_start = local_anchor.replace(
                            hour=0, minute=0, second=0, microsecond=0
                        )
                    else:
                        local_start = local_anchor.replace(
                            day=1, hour=0, minute=0, second=0, microsecond=0
                        )
                    result["window_start"] = iso(local_start.astimezone(timezone.utc))
            if "now" in result:
                result["now"] = iso(anchor)
            if "anchor_at" in result:
                result["anchor_at"] = iso(anchor)
            return result
        if isinstance(value, list):
            return [walk(item, key, inherited_window_label) for item in value]
        if key == "anchor_at":
            return shift(value, force_anchor=True)
        if key == "now":
            return iso(anchor)
        if key in {"window_start", "window_end", "now"}:
            return shift(value)
        return value

    return walk(arguments)


def _apply_acceptance_data_source(arguments: dict[str, Any]) -> dict[str, Any]:
    """Keep acceptance tool scopes on the copied simulated CGM source.

    Hermes may choose a familiar source label such as ``dexcom`` even when
    the isolated database contains a virtual fixture.  The acceptance runner
    supplies the actual source through an opt-in environment variable; normal
    deployments never enter this branch.
    """

    if os.getenv("CGM_AGENT_ENFORCE_DATA_SOURCE", "").strip() != "1":
        return arguments
    source = os.getenv("CGM_AGENT_ACCEPTANCE_SOURCE", "").strip()
    if not source:
        return arguments

    def walk(value: Any, key: str = "") -> Any:
        if isinstance(value, dict):
            result = {child_key: walk(child, child_key) for child_key, child in value.items()}
            if key == "data_scope":
                result["source"] = source
            return result
        if isinstance(value, list):
            return [walk(item, key) for item in value]
        return value

    return walk(arguments)


class ToolExecutor(
    TimeseriesHandlerMixin,
    EventHandlerMixin,
    MealCorrelationHandlerMixin,
    ContextHandlerMixin,
    ReportHandlerMixin,
    MemoryHandlerMixin,
    RagHandlerMixin,
    DeliveryHandlerMixin,
    PushTickHandlerMixin,
):
    """Routes a tool call to its per-domain handler (defined in the handler
    mixins) and owns the shared wiring: repository, audit service, registry,
    and the lazily-built rag seam."""

    def __init__(
        self,
        *,
        repository: SQLiteCGMRepository,
        audit_service: AuditService,
        registry: ToolRegistry | None = None,
    ) -> None:
        self.repository = repository
        self.audit_service = audit_service
        self.registry = registry or build_default_tool_registry()
        self._rag_tool_service: AuthoritativeRAGToolService | None = None
        # L-26: cached MemoryToolService (lazy init on first memory.* call).
        self._memory_tool_service = None

    _DISPATCH = {
        "timeseries.get_points": "_get_points",
        "timeseries.get_aggregate": "_get_aggregate",
        "timeseries.get_realtime_snapshot": "_get_realtime_snapshot",
        "events.create": "_create_event",
        "events.confirm": "_confirm_event",
        "meals.find_similar": "_find_similar_meals",
        "context.get_l0": "_get_l0_context",
        "reports.generate": "_generate_report",
        "memory.list": "_memory_list",
        "memory.delete": "_memory_delete",
        "memory.confirm": "_memory_confirm",
        "memory.correct": "_memory_correct",
        "rag.authoritative_search": "_rag_search",
        "rag.verify_quotes": "_verify_quotes",
        "kb.approve": "_kb_approve",
        "hypothesis.update": "_hypothesis_update",
        "delivery.send": "_delivery_send",
        "scheduling.push_tick": "_push_tick",
    }

    def execute(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        session_id: str,
    ) -> ToolExecutionResponse:
        # M-18: Schema validation is intentionally NOT enforced here at the
        # executor level. Per D054 (tolerant argument shape), real LLM callers
        # attach free text under ad-hoc keys (note/description/...) that the
        # strict JSON Schema would reject. Each handler validates its own
        # security-critical fields (user_id, data_scope, enum values, etc.)
        # inline, and unknown keys are folded into the documented freeform
        # payload field rather than failing the whole call. Adding a blanket
        # jsonschema.validate() here would break the tolerant-shape contract.
        try:
            spec = self.registry.get(tool_name)
        except KeyError as exc:
            return self._error_response(
                session_id=session_id,
                tool_name=tool_name,
                risk_level="unknown",
                data_scope=None,
                message=str(exc),
            )

        if spec.status != "active":
            return self._error_response(
                session_id=session_id,
                tool_name=tool_name,
                risk_level=spec.risk_level,
                data_scope=arguments.get("data_scope"),
                message=f"Tool is not active: {tool_name}",
            )

        handler_name = self._DISPATCH.get(tool_name)
        if handler_name is None:
            return self._error_response(
                session_id=session_id,
                tool_name=tool_name,
                risk_level=spec.risk_level,
                data_scope=arguments.get("data_scope"),
                message=f"Tool has no executor: {tool_name}",
            )

        handler = getattr(self, handler_name)
        arguments = _apply_acceptance_time_anchor(arguments)
        arguments = _apply_acceptance_data_source(arguments)
        if os.getenv("CGM_AGENT_ENFORCE_TIME_ANCHOR", "").strip() == "1":
            anchor = os.getenv("CGM_AGENT_ACCEPTANCE_ANCHOR_AT", "").strip()
            if anchor and tool_name == "context.get_l0":
                arguments.setdefault("anchor_at", anchor)
            elif anchor and tool_name == "timeseries.get_realtime_snapshot":
                arguments.setdefault("now", anchor)
            elif anchor and tool_name == "scheduling.push_tick":
                arguments.setdefault("now", anchor)
        arguments = _fill_default_user_id(arguments)
        try:
            return handler(arguments=arguments, session_id=session_id)
        except Exception as exc:  # noqa: BLE001 — the tool boundary must never
            # leak a raw traceback into the Hermes conversation. Handlers catch
            # their own expected error types; this is the last-resort net for
            # anything they missed (e.g. datetime naive/aware TypeErrors from
            # unexpected argument shapes). The failure is audited and returned
            # as a structured error the model can relay or retry on.
            return self._error_response(
                session_id=session_id,
                tool_name=tool_name,
                risk_level=spec.risk_level,
                data_scope=arguments.get("data_scope"),
                message=f"{type(exc).__name__}: {exc}",
            )
