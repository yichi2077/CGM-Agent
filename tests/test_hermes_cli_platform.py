from __future__ import annotations

import unittest

from hermes_cgm_agent.platform.base import ChatRequest
from hermes_cgm_agent.platform.hermes_cli import HermesCliPlatform
from hermes_cgm_agent.platform.local import LocalAgentPlatform


class HermesCliPlatformTests(unittest.TestCase):
    def test_build_chat_command_delegates_to_hermes_chat(self) -> None:
        platform = HermesCliPlatform()
        platform.hermes_bin = "hermes"
        command = platform._build_chat_command(
            ChatRequest(
                prompt="hello",
                model="test-model",
                provider="test-provider",
                toolsets="safe",
                skills="example",
                max_turns=3,
            )
        )

        self.assertEqual(command[:5], ["hermes", "chat", "--query", "hello", "--quiet"])
        self.assertIn("--source", command)
        self.assertIn("tool", command)
        self.assertIn("--model", command)
        self.assertIn("test-model", command)
        self.assertIn("--provider", command)
        self.assertIn("test-provider", command)
        self.assertIn("--toolsets", command)
        self.assertIn("safe", command)
        self.assertIn("--skills", command)
        self.assertIn("example", command)
        self.assertIn("--max-turns", command)
        self.assertIn("3", command)

    def test_local_platform_is_only_a_test_double(self) -> None:
        result = LocalAgentPlatform().chat(ChatRequest(prompt="ping"))
        self.assertTrue(result.ok)
        self.assertEqual(result.text, "[local-test-platform] ping")


if __name__ == "__main__":
    unittest.main()

