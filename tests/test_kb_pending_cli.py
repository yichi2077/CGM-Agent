from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from hermes_cgm_agent.cli import _kb_approve_cli, _kb_pending


class KbPendingCliTests(unittest.TestCase):
    def test_pending_lists_unverified_cards_without_claim_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            kb_path = self._write_kb(Path(temp_dir))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = _kb_pending(kb_path=kb_path, output_format="json", limit=None)
            payload = json.loads(stdout.getvalue())

        self.assertEqual(code, 0)
        self.assertEqual([row["card_id"] for row in payload["pending"]], ["auto-1"])
        self.assertEqual(payload["pending"][0]["title"], "Auto card")
        self.assertNotIn("claim_en", payload["pending"][0])
        self.assertNotIn("claim_zh", payload["pending"][0])

    def test_cli_approve_promotes_auto_card(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            kb_path = self._write_kb(Path(temp_dir))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = _kb_approve_cli(
                    kb_path=kb_path,
                    card_id="auto-1",
                    reviewer="Dr. X",
                    reviewed_at="2026-07-08T00:00:00+00:00",
                )
            payload = json.loads(stdout.getvalue())
            stored = json.loads(kb_path.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["verified"])
        self.assertEqual(payload["tier"], "auto")
        self.assertTrue(stored["cards"][0]["verified"])
        self.assertEqual(stored["cards"][0]["reviewer"], "Dr. X")

    def _write_kb(self, temp_dir: Path) -> Path:
        kb_path = temp_dir / "kb.json"
        kb_path.write_text(
            json.dumps(
                {
                    "kb_version": "kb-cli-test",
                    "cards": [
                        {
                            "card_id": "auto-1",
                            "title": "Auto card",
                            "claim_zh": "low glucose 70",
                            "claim_en": "low glucose 70",
                            "population": "general",
                            "tags": ["hypoglycemia"],
                            "synonyms": [],
                            "source": {"citation": "Test", "page": 1},
                            "verified": False,
                            "tier": "auto",
                        },
                        {
                            "card_id": "curated-verified",
                            "title": "Verified",
                            "claim_zh": "verified",
                            "claim_en": "verified",
                            "population": "general",
                            "tags": [],
                            "synonyms": [],
                            "source": {"citation": "Test", "page": 2},
                            "verified": True,
                            "reviewer": "Dr. Y",
                            "reviewed_at": "2026-07-08T00:00:00+00:00",
                            "tier": "curated",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        return kb_path


if __name__ == "__main__":
    unittest.main()
