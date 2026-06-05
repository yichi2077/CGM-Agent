from __future__ import annotations

import unittest

from hermes_cgm_agent.services.tools import ToolSpec, build_default_tool_registry


class ToolRegistryTests(unittest.TestCase):
    def test_default_registry_contains_core_cgm_tools(self) -> None:
        registry = build_default_tool_registry()
        names = {spec.name for spec in registry.list()}

        self.assertIn("timeseries.get_points", names)
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


if __name__ == "__main__":
    unittest.main()
