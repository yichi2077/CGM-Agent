from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hermes_cgm_agent.domain import L2ProfileItem
from hermes_cgm_agent.services.memory import SQLiteMemoryRepository, new_id
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class BiTemporalMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp_dir.name) / "app.db")
        self.store.initialize()
        self.mem = SQLiteMemoryRepository(self.store)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _item(self, value: dict, *, episode_ids: list[str]) -> L2ProfileItem:
        return L2ProfileItem(
            item_id=new_id(),
            user_id="user-1",
            key="breakfast_habit",
            value=value,
            source_episode_ids=episode_ids,
        )

    def test_lineage_round_trips(self) -> None:
        item = self._item({"summary": "常跳过早餐"}, episode_ids=["ep-1", "ep-2"])
        self.mem.upsert_profile_item(item)
        loaded = self.mem.list_profile_items("user-1")[0]
        self.assertEqual(loaded.source_episode_ids, ["ep-1", "ep-2"])
        self.assertIsNone(loaded.valid_to)  # currently valid

    def test_supersede_closes_old_window_and_time_travels(self) -> None:
        t0 = datetime(2026, 5, 1, tzinfo=timezone.utc)
        old = self._item({"summary": "以前不吃早餐"}, episode_ids=["ep-1"]).model_copy(
            update={"valid_from": t0}
        )
        self.mem.upsert_profile_item(old)

        switch = datetime(2026, 6, 1, tzinfo=timezone.utc)
        new = self._item({"summary": "现在每天吃早餐"}, episode_ids=["ep-9"])
        self.mem.supersede_profile_item(old_item_id=old.item_id, new_item=new, when=switch)

        # old window closed + lineage on the replacement
        reloaded = {i.item_id: i for i in self.mem.list_profile_items("user-1", active_only=False)}
        self.assertEqual(reloaded[old.item_id].valid_to, switch)
        self.assertFalse(reloaded[old.item_id].is_active)
        self.assertEqual(reloaded[new.item_id].supersedes_item_id, old.item_id)
        self.assertEqual(reloaded[new.item_id].valid_from, switch)

        # "now" sees only the new belief
        now_valid = self.mem.list_valid_profile_items("user-1")
        self.assertEqual([i.item_id for i in now_valid], [new.item_id])

        # time-travel: as of mid-May, only the old belief was valid
        as_of = datetime(2026, 5, 15, tzinfo=timezone.utc)
        past_valid = self.mem.list_valid_profile_items("user-1", as_of=as_of)
        self.assertEqual([i.item_id for i in past_valid], [old.item_id])


if __name__ == "__main__":
    unittest.main()
