from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hermes_cgm_agent.domain import L2ProfileItem
from hermes_cgm_agent.services.memory import (
    CGM_USER_MD_END,
    CGM_USER_MD_START,
    CGMMemoryProvider,
    SQLiteMemoryRepository,
    UserMDSyncService,
)
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class UserMDSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp_dir.name) / "app.db")
        self.store.initialize()
        self.repo = SQLiteMemoryRepository(self.store)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_sync_writes_managed_block_without_overwriting_existing_text(self) -> None:
        hermes_home = Path(self.temp_dir.name) / "hermes"
        hermes_home.mkdir()
        user_md = hermes_home / "USER.md"
        user_md.write_text("# User Notes\n\nKeep this line.\n", encoding="utf-8")
        self.repo.upsert_profile_item(
            L2ProfileItem(
                item_id="pi-1",
                user_id="user-1",
                key="pattern:breakfast",
                value={"summary": "Breakfast tends to be stable."},
                confidence=0.8,
                evidence_count=4,
            )
        )

        result = UserMDSyncService(repository=self.repo).sync(
            user_id="user-1",
            hermes_home=hermes_home,
        )
        content = user_md.read_text(encoding="utf-8")

        self.assertTrue(result.wrote)
        self.assertIn("Keep this line.", content)
        self.assertIn(CGM_USER_MD_START, content)
        self.assertIn(CGM_USER_MD_END, content)
        self.assertIn("Breakfast tends to be stable.", content)

    def test_sync_replaces_only_managed_block_and_preserves_surrounding_text(self) -> None:
        hermes_home = Path(self.temp_dir.name) / "hermes-replace"
        hermes_home.mkdir()
        user_md = hermes_home / "USER.md"
        user_md.write_text(
            "\n".join(
                [
                    "# User Notes",
                    "",
                    "Keep this before.",
                    "",
                    CGM_USER_MD_START,
                    "# Old CGM Profile Memory",
                    "",
                    "- stale managed line",
                    "",
                    CGM_USER_MD_END,
                    "",
                    "Keep this after.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        self.repo.upsert_profile_item(
            L2ProfileItem(
                item_id="pi-1",
                user_id="user-1",
                key="pattern:dinner",
                value={"summary": "Dinner tends to rise."},
                confidence=0.7,
                evidence_count=3,
            )
        )

        result = UserMDSyncService(repository=self.repo).sync(
            user_id="user-1",
            hermes_home=hermes_home,
        )
        content = user_md.read_text(encoding="utf-8")

        self.assertTrue(result.wrote)
        self.assertIn("Keep this before.", content)
        self.assertIn("Keep this after.", content)
        self.assertIn("Dinner tends to rise.", content)
        self.assertNotIn("stale managed line", content)
        self.assertEqual(content.count(CGM_USER_MD_START), 1)
        self.assertEqual(content.count(CGM_USER_MD_END), 1)

    def test_provider_session_end_syncs_l2_to_user_md(self) -> None:
        hermes_home = Path(self.temp_dir.name) / "hermes-provider"
        self.repo.upsert_profile_item(
            L2ProfileItem(
                item_id="pi-1",
                user_id="user-1",
                key="pattern:lunch",
                value={"summary": "Lunch often rises."},
            )
        )
        provider = CGMMemoryProvider(self.store, user_id="user-1")
        provider.initialize(
            session_id="session-1",
            user_id="user-1",
            hermes_home=str(hermes_home),
        )

        provider.on_session_end([])

        content = (hermes_home / "USER.md").read_text(encoding="utf-8")
        self.assertIn("Lunch often rises.", content)


if __name__ == "__main__":
    unittest.main()
