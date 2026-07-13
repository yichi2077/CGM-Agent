"""Unit tests for MemoryContextAssembler and D031 numeric-conflict detection.

Covers G2 (resolve_conflict runtime wiring) and the assembler-level gap noted
in the audit: build_memory_context previously had only indirect coverage.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from hermes_cgm_agent.domain import (
    EvidenceRef,
    HypothesisState,
    L1Episode,
    L2ProfileItem,
    L3Hypothesis,
)
from hermes_cgm_agent.domain.report import MemoryContext
from hermes_cgm_agent.services.memory import (
    MemoryContextAssembler,
    SQLiteMemoryRepository,
    new_id,
)
from hermes_cgm_agent.services.memory.assembler import (
    _extract_glucose_ranges,
    detect_numeric_conflicts,
)
from hermes_cgm_agent.storage.sqlite import SQLiteStore

NOW = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)

TARGET_RANGE_DOC = {
    "title": "TIR 目标范围",
    "text": "对多数成年人，目标范围 70-180 mg/dL，TIR 建议 >70%。",
    "evidence_refs": [
        {"kind": "authoritative_kb", "ref_id": "kb-tir-1", "summary": "TIR"}
    ],
}


class AssemblerContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        store = SQLiteStore(Path(self.temp_dir.name) / "app.db")
        store.initialize()
        self.mem = SQLiteMemoryRepository(store)
        self.assembler = MemoryContextAssembler(repository=self.mem)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _seed_episode(self, summary: str = "Lunch caused a glucose spike") -> None:
        self.mem.create_episode(
            L1Episode(
                episode_id=new_id(),
                user_id="user-1",
                occurred_at=NOW,
                episode_type="postprandial_spike",
                summary=summary,
                evidence_refs=[EvidenceRef(kind="event", ref_id="ev-1")],
                confidence=0.8,
            )
        )

    def test_build_memory_context_empty(self) -> None:
        ctx = self.assembler.build_memory_context(user_id="user-1", query="anything")
        self.assertTrue(ctx.enabled)
        self.assertEqual(ctx.items, [])
        self.assertEqual(ctx.missing_reason, "no_user_memory_yet")
        self.assertEqual(ctx.conflict_resolutions, [])

    def test_build_memory_context_with_items(self) -> None:
        self._seed_episode()
        ctx = self.assembler.build_memory_context(user_id="user-1", query="lunch spike")
        self.assertTrue(ctx.items)
        self.assertEqual(ctx.items[0]["layer"], "L1")
        self.assertEqual(ctx.items[0]["evidence_refs"][0]["kind"], "user_memory")

    def test_build_memory_context_hot_items_injected_without_match(self) -> None:
        self.mem.upsert_profile_item(
            L2ProfileItem(
                item_id=new_id(),
                user_id="user-1",
                key="breakfast_habit",
                value={"summary": "常跳过早餐"},
                confidence=0.9,
            )
        )
        self.mem.upsert_hypothesis(
            L3Hypothesis(
                hypothesis_id=new_id(),
                user_id="user-1",
                statement="周五晚餐后血糖易偏高",
                state=HypothesisState.OBSERVING,
            )
        )
        ctx = self.assembler.build_memory_context(
            user_id="user-1", query="zzz-no-lexical-match"
        )
        hot_layers = {item["layer"] for item in ctx.items if item.get("hot")}
        self.assertEqual(hot_layers, {"L2", "L3"})

    def test_build_authoritative_context_documents(self) -> None:
        ctx = self.assembler.build_authoritative_context(query="time in range")
        self.assertTrue(ctx.documents)
        self.assertEqual(
            ctx.documents[0].evidence_refs[0].kind, "authoritative_kb"
        )

    def test_build_memory_context_track_isolation(self) -> None:
        # Every item produced by the personal track must carry user_memory
        # evidence only — assert_track_isolation runs inside and must not raise.
        self._seed_episode()
        ctx = self.assembler.build_memory_context(user_id="user-1", query="lunch")
        for item in ctx.items:
            kinds = {ref["kind"] for ref in item["evidence_refs"]}
            self.assertEqual(kinds, {"user_memory"})


class NumericConflictDetectionTests(unittest.TestCase):
    def _personal(self, summary: str) -> dict:
        return {
            "summary": summary,
            "layer": "L2",
            "evidence_refs": [
                {"kind": "user_memory", "ref_id": "p-1", "summary": summary}
            ],
        }

    def test_disjoint_ranges_resolve_to_authoritative(self) -> None:
        # 12-15 mmol/L = 216-270 mg/dL, disjoint from KB 70-180 mg/dL.
        item = self._personal("我的血糖通常在 12-15 mmol/L，感觉没什么问题")
        resolutions = detect_numeric_conflicts([item], [TARGET_RANGE_DOC])
        self.assertEqual(len(resolutions), 1)
        self.assertEqual(resolutions[0]["winner"], "authoritative")
        self.assertIn("温和", resolutions[0]["note"])
        self.assertEqual(resolutions[0]["personal"]["summary"], item["summary"])

    def test_overlapping_ranges_are_not_conflicts(self) -> None:
        # 5-8 mmol/L = 90-144 mg/dL, inside KB 70-180 mg/dL.
        item = self._personal("我的血糖通常在 5-8 mmol/L")
        self.assertEqual(detect_numeric_conflicts([item], [TARGET_RANGE_DOC]), [])

    def test_unitless_low_values_inferred_as_mmol(self) -> None:
        # "血糖通常在 15-18" — unitless but glucose keyword present; 15-18
        # inferred as mmol/L = 270-324 mg/dL → disjoint from 70-180 → conflict.
        item = self._personal("我的血糖通常在 15-18，一直这样")
        resolutions = detect_numeric_conflicts([item], [TARGET_RANGE_DOC])
        self.assertEqual(len(resolutions), 1)

    def test_non_glucose_ranges_ignored(self) -> None:
        item = self._personal("每周运动 3-5 次，睡眠 7-8 小时")
        self.assertEqual(detect_numeric_conflicts([item], [TARGET_RANGE_DOC]), [])

    def test_no_documents_no_conflicts(self) -> None:
        item = self._personal("我的血糖通常在 12-15 mmol/L")
        self.assertEqual(detect_numeric_conflicts([item], []), [])

    def test_hypoglycemia_threshold_is_not_treated_as_target_range(self) -> None:
        item = self._personal("My glucose is usually 80-120 mg/dL")
        threshold_doc = {
            "title": "Hypoglycemia thresholds",
            "text": "Hypoglycemia level 2 is 54-70 mg/dL.",
            "evidence_refs": TARGET_RANGE_DOC["evidence_refs"],
        }
        self.assertEqual(detect_numeric_conflicts([item], [threshold_doc]), [])

    def test_mixed_kb_ranges_compare_only_explicit_target_range(self) -> None:
        item = self._personal("My glucose is usually 80-120 mg/dL")
        mixed_doc = {
            "title": "Glucose ranges",
            "text": (
                "The target range is 70-180 mg/dL. "
                "Level 2 hypoglycemia is 54-70 mg/dL; severe hyperglycemia is 250-400 mg/dL."
            ),
            "evidence_refs": TARGET_RANGE_DOC["evidence_refs"],
        }
        self.assertEqual(detect_numeric_conflicts([item], [mixed_doc]), [])

    def test_unitless_non_glucose_range_in_other_clause_is_ignored(self) -> None:
        text = "My glucose is stable. I exercise 3-5 times each week."
        self.assertEqual(_extract_glucose_ranges(text), [])

    def test_extract_ranges_normalizes_units(self) -> None:
        ranges = _extract_glucose_ranges("目标范围 70-180 mg/dL")
        self.assertEqual(ranges, [(70.0, 180.0)])
        (low, high), = _extract_glucose_ranges("血糖 4-6 mmol/L")
        self.assertAlmostEqual(low, 4 * 18.016)
        self.assertAlmostEqual(high, 6 * 18.016)

    def test_memory_context_round_trips_conflict_resolutions(self) -> None:
        item = self._personal("我的血糖通常在 12-15 mmol/L")
        resolutions = detect_numeric_conflicts([item], [TARGET_RANGE_DOC])
        ctx = MemoryContext(enabled=True, items=[item], conflict_resolutions=resolutions)
        dumped = ctx.model_dump(mode="json")
        restored = MemoryContext.model_validate(dumped)
        self.assertEqual(len(restored.conflict_resolutions), 1)
        self.assertEqual(restored.conflict_resolutions[0]["winner"], "authoritative")


if __name__ == "__main__":
    unittest.main()
