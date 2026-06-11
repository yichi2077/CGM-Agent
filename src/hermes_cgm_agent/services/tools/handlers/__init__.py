"""Per-domain tool handler mixins for ToolExecutor.

Each domain lives in its own module so independent feature work (e.g. F3 rag,
F4 reports, F5 delivery) can edit different files without contention. The
mixins all read shared executor state (`repository`, `audit_service`,
`registry`) and the shared error path through `BaseToolHandler`; ToolExecutor
composes them and owns `__init__` + the dispatch table.
"""

from __future__ import annotations

from hermes_cgm_agent.services.tools.handlers.base import (
    BaseToolHandler,
    ToolExecutionResponse,
)
from hermes_cgm_agent.services.tools.handlers.context import ContextHandlerMixin
from hermes_cgm_agent.services.tools.handlers.delivery import DeliveryHandlerMixin
from hermes_cgm_agent.services.tools.handlers.dexcom import DexcomHandlerMixin
from hermes_cgm_agent.services.tools.handlers.events import EventHandlerMixin
from hermes_cgm_agent.services.tools.handlers.memory import MemoryHandlerMixin
from hermes_cgm_agent.services.tools.handlers.push_tick import PushTickHandlerMixin
from hermes_cgm_agent.services.tools.handlers.rag import RagHandlerMixin
from hermes_cgm_agent.services.tools.handlers.reports import ReportHandlerMixin
from hermes_cgm_agent.services.tools.handlers.timeseries import TimeseriesHandlerMixin

__all__ = [
    "BaseToolHandler",
    "ToolExecutionResponse",
    "ContextHandlerMixin",
    "DeliveryHandlerMixin",
    "DexcomHandlerMixin",
    "EventHandlerMixin",
    "MemoryHandlerMixin",
    "PushTickHandlerMixin",
    "RagHandlerMixin",
    "ReportHandlerMixin",
    "TimeseriesHandlerMixin",
]
