"""G4: semantic tool status codes (ToolStatus) beyond the ok/error binary.

Locks the new statuses: no_data (successful query, empty result), partial
(delivery attempted, remote leg incomplete), not_found (missing resource),
rate_limited (upstream throttling), and the registry output-schema update.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.tools import ToolExecutor
from hermes_cgm_agent.services.tools.handlers.base import (
    FAILURE_STATUSES,
    ToolExecutionResponse,
    ToolStatus,
)
from hermes_cgm_agent.services.tools.registry import build_default_tool_registry
from hermes_cgm_agent.storage.sqlite import SQLiteStore

EMPTY_SCOPE = {
    "user_id": "user-1",
    "window_start": "2026-05-31T00:00:00+00:00",
    "window_end": "2026-06-01T00:00:00+00:00",
}


class ToolStatusEnumTests(unittest.TestCase):
    def test_values_are_wire_strings(self) -> None:
        self.assertEqual(ToolStatus.OK.value, "ok")
        self.assertEqual(ToolStatus.NO_DATA.value, "no_data")
        self.assertEqual(ToolStatus.PARTIAL.value, "partial")
        self.assertEqual(ToolStatus.NOT_FOUND.value, "not_found")
        self.assertEqual(ToolStatus.RATE_LIMITED.value, "rate_limited")
        self.assertEqual(ToolStatus.ERROR.value, "error")

    def test_failure_statuses_exclude_soft_outcomes(self) -> None:
        self.assertEqual(FAILURE_STATUSES, {"error", "not_found", "rate_limited"})

    def test_response_normalizes_string_status_to_enum(self) -> None:
        response = ToolExecutionResponse(
            status="ok",
            evidence_refs=[],
            audit_id=None,
            payload={},
        )
        self.assertIs(response.status, ToolStatus.OK)
        self.assertEqual(response.to_dict()["status"], "ok")

    def test_response_rejects_status_outside_public_contract(self) -> None:
        with self.assertRaises(ValueError):
            ToolExecutionResponse(
                status="queued",
                evidence_refs=[],
                audit_id=None,
                payload={},
            )

    def test_registry_output_schema_lists_all_statuses(self) -> None:
        registry = build_default_tool_registry()
        spec = registry.get("timeseries.get_points")
        enum = spec.output_schema["properties"]["status"]["enum"]
        self.assertEqual(
            set(enum),
            {"ok", "no_data", "partial", "not_found", "rate_limited", "error"},
        )


class ToolStatusExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp_dir.name) / "app.db")
        self.store.initialize()
        self.repository = SQLiteCGMRepository(self.store)
        self.executor = ToolExecutor(
            repository=self.repository,
            audit_service=AuditService(self.store),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _execute(self, tool_name: str, arguments: dict):
        return self.executor.execute(
            tool_name=tool_name, arguments=arguments, session_id="status-test"
        )

    def test_get_points_empty_window_is_no_data(self) -> None:
        response = self._execute("timeseries.get_points", {"data_scope": EMPTY_SCOPE})
        self.assertEqual(response.status, "no_data")
        self.assertEqual(response.to_dict()["points"], [])

    def test_get_aggregate_empty_window_is_no_data(self) -> None:
        response = self._execute("timeseries.get_aggregate", {"data_scope": EMPTY_SCOPE})
        self.assertEqual(response.status, "no_data")

    def test_memory_list_empty_is_no_data(self) -> None:
        response = self._execute("memory.list", {"user_id": "user-1", "layer": "all"})
        self.assertEqual(response.status, "no_data")
        self.assertEqual(response.to_dict()["total_count"], 0)

    def test_memory_delete_unknown_record_is_not_found(self) -> None:
        response = self._execute(
            "memory.delete",
            {"user_id": "user-1", "memory_id": "no-such-id", "layer": "L1"},
        )
        self.assertEqual(response.status, "not_found")
        self.assertIn("Unknown memory record", response.to_dict()["error"])

    def test_rag_search_no_match_is_no_data(self) -> None:
        response = self._execute(
            "rag.authoritative_search",
            {"query": "zzz-nonexistent-topic-qqqq", "top_k": 3},
        )
        self.assertEqual(response.status, "no_data")

    def test_rag_search_with_match_stays_ok(self) -> None:
        response = self._execute(
            "rag.authoritative_search", {"query": "time in range", "top_k": 3}
        )
        self.assertEqual(response.status, "ok")

    def test_webhook_not_configured_is_partial(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("CGM_WEBHOOK_URL", None)
            response = self._execute(
                "delivery.send",
                {"user_id": "user-1", "channel": "webhook", "payload_ref": "push-1"},
            )
        self.assertEqual(response.status, "partial")
        self.assertEqual(response.to_dict()["delivery_status"], "failed")


if __name__ == "__main__":
    unittest.main()
