from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hermes_cgm_agent.domain import (
    GlucosePoint,
    GlucoseUnit,
    QualityFlag,
)
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.memory.provider import CGMMemoryProvider, _looks_memory_relevant
from hermes_cgm_agent.storage.sqlite import SQLiteStore

_SOUL_PATH = Path(__file__).resolve().parents[1] / "SOUL.md"


class MemoryProviderTests(unittest.TestCase):
    def test_system_prompt_includes_compact_soul_persona(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteStore(Path(temp_dir) / "app.db")
            store.initialize()
            provider = CGMMemoryProvider(store, user_id="u1")

            out = provider.system_prompt_block()

        self.assertIn("SOUL.md", out)
        self.assertIn("知情陪伴者", out)
        self.assertIn("cgm_reports_generate", out)
        # D2/D3: instructions in Chinese, audience defaults to SELF.
        self.assertIn("audience='SELF'", out)
        self.assertNotIn("audience='CLINICIAN'", out)
        # D1: compact can be up to 2500 chars + instruction text ~150.
        self.assertLess(len(out), 2700)

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


class CompactSoulTests(unittest.TestCase):
    """D1: SOUL.md compaction preserves key sections and respects the cap."""

    def test_compact_soul_preserves_priority_sections(self) -> None:
        """High-priority section headers must survive compaction."""
        soul_text = _SOUL_PATH.read_text(encoding="utf-8")
        provider = CGMMemoryProvider.__new__(CGMMemoryProvider)
        compact = provider._compact_soul(soul_text)

        # Priority sections: 角色定义, 交互原则, 语言风格, 安全边界.
        for section in ("## 我是谁", "## 交互原则", "## 语言风格", "## 安全边界"):
            self.assertIn(section, compact, f"Missing priority section: {section}")

    def test_compact_soul_preserves_example_pairs(self) -> None:
        """The ✅ / ❌ example pairs must be present (compacted to single lines)."""
        soul_text = _SOUL_PATH.read_text(encoding="utf-8")
        provider = CGMMemoryProvider.__new__(CGMMemoryProvider)
        compact = provider._compact_soul(soul_text)

        # At least one ✅ and one ❌ should survive.
        self.assertIn("✅", compact)
        self.assertIn("❌", compact)

    def test_compact_soul_under_hard_cap(self) -> None:
        """Compacted output must stay within the 2500-char hard cap."""
        soul_text = _SOUL_PATH.read_text(encoding="utf-8")
        provider = CGMMemoryProvider.__new__(CGMMemoryProvider)
        compact = provider._compact_soul(soul_text)
        self.assertLessEqual(len(compact), 2500)

    def test_compact_soul_nonempty(self) -> None:
        """Compacted output must not be empty for the real SOUL.md."""
        soul_text = _SOUL_PATH.read_text(encoding="utf-8")
        provider = CGMMemoryProvider.__new__(CGMMemoryProvider)
        compact = provider._compact_soul(soul_text)
        self.assertGreater(len(compact), 500)

    def test_compact_soul_keeps_user_protection_boundary(self) -> None:
        soul_text = _SOUL_PATH.read_text(encoding="utf-8")
        provider = CGMMemoryProvider.__new__(CGMMemoryProvider)
        compact = provider._compact_soul(soul_text)
        self.assertIn("情感优先于数据", compact)
        self.assertIn("记忆衰减权", compact)


class ReportAudienceTests(unittest.TestCase):
    """D3: /report defaults to SELF, not CLINICIAN."""

    def test_report_defaults_to_self(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteStore(Path(temp_dir) / "app.db")
            store.initialize()
            provider = CGMMemoryProvider(store, user_id="u1")
            out = provider.system_prompt_block()
        self.assertIn("audience='SELF'", out)
        self.assertNotIn("audience='CLINICIAN'", out)


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


class RealtimeStatusTests(unittest.TestCase):
    """D4: real-time CGM status injection in prefetch()."""

    def test_realtime_status_empty_store(self) -> None:
        """Empty store returns empty status (defer to empty-store hint)."""
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteStore(Path(temp_dir) / "app.db")
            store.initialize()
            provider = CGMMemoryProvider(store, user_id="u1")
            status = provider._build_realtime_status()
        self.assertEqual(status, "")

    def test_realtime_status_no_recent_data(self) -> None:
        """Store with old data reports sensor disconnected."""
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteStore(Path(temp_dir) / "app.db")
            store.initialize()
            repo = SQLiteCGMRepository(store)
            # Insert a point from 3 hours ago (outside the 1-hour window).
            old_ts = datetime.now(timezone.utc) - timedelta(hours=3)
            repo.create_glucose_point(
                GlucosePoint(
                    user_id="u1",
                    timestamp=old_ts,
                    value=120,
                    unit=GlucoseUnit.MG_DL,
                    source="test",
                    quality_flag=QualityFlag.VALID,
                )
            )
            provider = CGMMemoryProvider(store, user_id="u1")
            status = provider._build_realtime_status()
        self.assertIn("最近 1 小时无新数据", status)
        self.assertIn("传感器未连接", status)

    def test_realtime_status_with_recent_data(self) -> None:
        """Store with recent data reports current value, trend, and range."""
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteStore(Path(temp_dir) / "app.db")
            store.initialize()
            repo = SQLiteCGMRepository(store)
            now = datetime.now(timezone.utc).replace(microsecond=0)
            # Insert two points within the last hour.
            repo.create_glucose_point(
                GlucosePoint(
                    user_id="u1",
                    timestamp=now - timedelta(minutes=20),
                    value=110,
                    unit=GlucoseUnit.MG_DL,
                    source="test",
                    quality_flag=QualityFlag.VALID,
                )
            )
            repo.create_glucose_point(
                GlucosePoint(
                    user_id="u1",
                    timestamp=now - timedelta(minutes=5),
                    value=120,
                    unit=GlucoseUnit.MG_DL,
                    source="test",
                    quality_flag=QualityFlag.VALID,
                )
            )
            provider = CGMMemoryProvider(store, user_id="u1")
            status = provider._build_realtime_status()
        self.assertIn("当前状态", status)
        self.assertIn("mmol/L", status)
        self.assertIn("mg/dL", status)
        self.assertIn("趋势", status)

    def test_realtime_status_ignores_suspect_latest_reading(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteStore(Path(temp_dir) / "app.db")
            store.initialize()
            repo = SQLiteCGMRepository(store)
            now = datetime.now(timezone.utc).replace(microsecond=0)
            repo.create_glucose_point(
                GlucosePoint(
                    user_id="u1", timestamp=now - timedelta(minutes=5), value=120,
                    unit=GlucoseUnit.MG_DL, source="test", quality_flag=QualityFlag.VALID,
                )
            )
            repo.create_glucose_point(
                GlucosePoint(
                    user_id="u1", timestamp=now - timedelta(minutes=1), value=330,
                    unit=GlucoseUnit.MG_DL, source="test", quality_flag=QualityFlag.SUSPECT,
                )
            )
            status = CGMMemoryProvider(store, user_id="u1")._build_realtime_status()
        self.assertIn("120 mg/dL", status)
        self.assertNotIn("330 mg/dL", status)

    def test_realtime_status_marks_within_hour_stale_data_not_current(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteStore(Path(temp_dir) / "app.db")
            store.initialize()
            repo = SQLiteCGMRepository(store)
            repo.create_glucose_point(
                GlucosePoint(
                    user_id="u1",
                    timestamp=datetime.now(timezone.utc) - timedelta(minutes=20),
                    value=48,
                    unit=GlucoseUnit.MG_DL,
                    source="test",
                    quality_flag=QualityFlag.VALID,
                )
            )
            status = CGMMemoryProvider(store, user_id="u1")._build_realtime_status()
        self.assertIn("当前数据可能已过期", status)
        self.assertNotIn("48 mg/dL", status)

    def test_prefetch_empty_hint_is_scoped_to_current_user(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteStore(Path(temp_dir) / "app.db")
            store.initialize()
            SQLiteCGMRepository(store).create_glucose_point(
                GlucosePoint(
                    user_id="other-user",
                    timestamp=datetime.now(timezone.utc),
                    value=120,
                    unit=GlucoseUnit.MG_DL,
                    source="test",
                    quality_flag=QualityFlag.VALID,
                )
            )
            out = CGMMemoryProvider(store, user_id="u1").prefetch("最近血糖怎么样")
        self.assertIn("empty store", out)

    def test_prefetch_includes_realtime_status(self) -> None:
        """prefetch() includes [CGM 实时状态] line when data exists."""
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteStore(Path(temp_dir) / "app.db")
            store.initialize()
            repo = SQLiteCGMRepository(store)
            now = datetime.now(timezone.utc).replace(microsecond=0)
            repo.create_glucose_point(
                GlucosePoint(
                    user_id="u1",
                    timestamp=now - timedelta(minutes=5),
                    value=120,
                    unit=GlucoseUnit.MG_DL,
                    source="test",
                    quality_flag=QualityFlag.VALID,
                )
            )
            provider = CGMMemoryProvider(store, user_id="u1")
            out = provider.prefetch("最近血糖怎么样")
        self.assertIn("[CGM 实时状态]", out)
        self.assertIn("当前状态", out)

    def test_on_session_end_swallows_consolidation_error(self) -> None:
        """H-06: consolidation failure on session end must not leak _session_turns."""
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteStore(Path(temp_dir) / "app.db")
            store.initialize()
            provider = CGMMemoryProvider(store, user_id="u1")
            provider._session_id = "test-session"
            provider._session_turns["test-session"] = 3

            # Force consolidate to fail.
            original_consolidate = provider._consolidation.consolidate
            provider._consolidation.consolidate = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom"))  # type: ignore

            try:
                # Should not raise.
                provider.on_session_end([])
            finally:
                provider._consolidation.consolidate = original_consolidate  # type: ignore

            # _session_turns must still be cleaned up.
            self.assertNotIn("test-session", provider._session_turns)


if __name__ == "__main__":
    unittest.main()
