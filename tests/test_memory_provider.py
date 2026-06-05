from __future__ import annotations

import unittest

from hermes_cgm_agent.services.memory.provider import _looks_memory_relevant


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
