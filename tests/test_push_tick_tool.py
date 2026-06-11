from __future__ import annotations

import json
import unittest

from hermes_cgm_agent.services.tools import build_default_tool_registry


class PushTickRegistrationTests(unittest.TestCase):
    """T002 / C1 / FR-001: push_tick is a registered, active, self-contained
    tool following the dotted ``group.action`` naming convention used by every
    other tool (``delivery.send``, ``data.dexcom_sync``) -> ``scheduling.push_tick``."""

    def setUp(self) -> None:
        self.registry = build_default_tool_registry()

    def _spec(self):
        spec = next(
            (s for s in self.registry.list() if s.name == "scheduling.push_tick"),
            None,
        )
        self.assertIsNotNone(spec, "scheduling.push_tick is not registered")
        return spec

    def test_push_tick_is_active_in_scheduling_group(self) -> None:
        names = {spec.name for spec in self.registry.list()}
        self.assertIn("scheduling.push_tick", names)
        spec = self._spec()
        self.assertEqual(spec.group, "scheduling")
        self.assertEqual(spec.status, "active")
        self.assertEqual(spec.owner_module, "push_scheduler")

    def test_push_tick_input_requires_user_id_and_optional_now(self) -> None:
        spec = self._spec()
        schema = spec.input_schema
        self.assertIn("user_id", schema["properties"])
        self.assertIn("user_id", schema["required"])
        self.assertIn("now", schema["properties"])
        self.assertNotIn("now", schema["required"])
        # Self-contained schema: no unresolved $ref/$defs (model must resolve it).
        self.assertNotIn("$ref", json.dumps(schema))

    def test_push_tick_output_exposes_pushed_and_silent_consent(self) -> None:
        spec = self._spec()
        props = spec.output_schema["properties"]
        self.assertIn("pushed", props)
        self.assertIn("silent_consent", props)


if __name__ == "__main__":
    unittest.main()
