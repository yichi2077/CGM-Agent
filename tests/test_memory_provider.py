from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hermes_cgm_agent.services.memory.provider import CGMMemoryProvider, _looks_memory_relevant
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class MemoryProviderTests(unittest.TestCase):
    def test_memory_relevant_keywords_cover_new_categories(self) -> None:
        samples = [
            "今天血糖有点乱，我很焦虑。",
            "晚上吃了蛋糕和奶茶。",
            "这两天一直失眠，睡得晚。",
            "今天开始吃药了，二甲双胍先继续。",
            "最近压力大，还有点发烧，不舒服。",
        ]

        for sample in samples:
            with self.subTest(sample=sample):
                self.assertTrue(_looks_memory_relevant(sample))


class EmptyStorePrefetchTests(unittest.TestCase):
    """F1 A5: an empty store guides the agent (gently) to import/seed."""

    def test_prefetch_hints_when_store_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteStore(Path(temp_dir) / "app.db")
            store.initialize()
            provider = CGMMemoryProvider(store, user_id="u1")
            out = provider.prefetch("最近血糖怎么样")
        self.assertIn("empty store", out)
        self.assertIn("import-cgm", out)
