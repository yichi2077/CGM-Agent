from __future__ import annotations

import json
import unittest

from hermes_cgm_agent.services.tools import ToolSpec, build_default_tool_registry


class ToolRegistryTests(unittest.TestCase):
    def test_default_registry_contains_core_cgm_tools(self) -> None:
        registry = build_default_tool_registry()
        names = {spec.name for spec in registry.list()}

        self.assertIn("timeseries.get_points", names)
        self.assertIn("context.get_l0", names)
        self.assertIn("events.create", names)
        self.assertIn("reports.generate", names)
        self.assertIn("memory.list", names)
        self.assertIn("memory.delete", names)
        self.assertIn("memory.correct", names)
        self.assertIn("rag.authoritative_search", names)

    def test_tool_specs_include_audit_and_scope_contracts(self) -> None:
        registry = build_default_tool_registry()
        spec = registry.get("timeseries.get_points")
        aggregate_spec = registry.get("timeseries.get_aggregate")

        self.assertEqual(spec.status, "active")
        self.assertEqual(aggregate_spec.status, "active")
        self.assertTrue(spec.writes_audit)
        self.assertTrue(spec.evidence_required)
        self.assertIn("data_scope", spec.input_schema["properties"])
        self.assertIn("evidence_refs", spec.output_schema["properties"])

    def test_memory_confirm_schema_matches_executor_payload(self) -> None:
        # C8: schema must advertise candidate_status (what the executor returns),
        # not a second "status" colliding with the ok/error envelope.
        registry = build_default_tool_registry()
        props = registry.get("memory.confirm").output_schema["properties"]

        self.assertIn("candidate_status", props)
        self.assertIn("candidate_id", props)

    def test_hypothesis_update_schema_uses_archived_state(self) -> None:
        registry = build_default_tool_registry()
        states = registry.get("hypothesis.update").input_schema["properties"]["state"]["enum"]

        self.assertIn("archived", states)
        self.assertNotIn("invalid", states)

    def test_events_confirm_requires_user_id(self) -> None:
        # C2: ownership argument is part of the tool contract.
        registry = build_default_tool_registry()
        schema = registry.get("events.confirm").input_schema

        self.assertIn("user_id", schema["properties"])
        self.assertIn("user_id", schema["required"])

    def test_tool_schemas_have_no_unresolved_refs(self) -> None:
        # C2 / F1: dangling "$ref": "#/$defs/..." entries (no $defs block exists) make
        # tool schemas unresolvable for the model. All schemas must be self-contained.
        registry = build_default_tool_registry()
        blob = json.dumps(
            [{"in": spec.input_schema, "out": spec.output_schema} for spec in registry.list()]
        )
        self.assertNotIn("$ref", blob)

    def test_events_create_event_is_inline_minimal_object(self) -> None:
        registry = build_default_tool_registry()
        event_schema = registry.get("events.create").input_schema["properties"]["event"]
        self.assertEqual(event_schema["type"], "object")
        self.assertEqual(set(event_schema["required"]), {"event_type", "ts_start"})
        self.assertNotIn("$ref", json.dumps(event_schema))

    def test_duplicate_tool_names_are_rejected(self) -> None:
        registry = build_default_tool_registry()
        spec = registry.get("events.create")
        duplicate = ToolSpec(
            name=spec.name,
            group=spec.group,
            description=spec.description,
            input_schema=spec.input_schema,
            output_schema=spec.output_schema,
            owner_module=spec.owner_module,
        )

        with self.assertRaises(ValueError):
            registry.register(duplicate)


class ExecutorDispatchCoverageTests(unittest.TestCase):
    """G1 guard: every active tool the registry exposes must have a handler
    wired in ToolExecutor._DISPATCH, and every dispatch entry must map to a
    real method. After splitting the executor into per-domain handler mixins,
    this catches a tool added during parallel feature work (F3/F4/F5) whose
    handler was never wired — which would otherwise only surface as a runtime
    "Tool has no executor" error."""

    def test_every_active_tool_has_a_dispatch_handler(self) -> None:
        from hermes_cgm_agent.services.tools import ToolExecutor

        registry = build_default_tool_registry()
        active = {spec.name for spec in registry.list() if spec.status == "active"}
        dispatched = set(ToolExecutor._DISPATCH)
        self.assertEqual(active - dispatched, set(), "active tools missing a handler")
        self.assertEqual(dispatched - active, set(), "dispatch entries with no active tool")

    def test_every_dispatch_handler_method_exists(self) -> None:
        from hermes_cgm_agent.services.tools import ToolExecutor

        for tool_name, method_name in ToolExecutor._DISPATCH.items():
            self.assertTrue(
                callable(getattr(ToolExecutor, method_name, None)),
                f"{tool_name} -> {method_name} is not a callable handler",
            )


if __name__ == "__main__":
    unittest.main()
