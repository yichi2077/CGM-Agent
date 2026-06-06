from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from hermes_cgm_agent.domain import EvidenceRef, L1Episode
from hermes_cgm_agent.services.memory import (
    CGMMemoryProvider,
    ConsolidationService,
    SQLiteMemoryRepository,
    new_id,
)
from hermes_cgm_agent.storage.sqlite import SQLiteStore

NOW = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)


class WarmSynthesisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp_dir.name) / "app.db")
        self.store.initialize()
        self.mem = SQLiteMemoryRepository(self.store)
        self.consolidation = ConsolidationService(repository=self.mem)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _seed_episode(self) -> None:
        self.mem.create_episode(
            L1Episode(
                episode_id=new_id(),
                user_id="user-1",
                occurred_at=NOW,
                episode_type="postprandial_spike",
                summary="晚餐后血糖偏高",
                evidence_refs=[EvidenceRef(kind="event", ref_id="ev-1")],
            )
        )

    def test_synthesize_state_persists_summary_with_metrics(self) -> None:
        self._seed_episode()
        summary = self.consolidation.synthesize_state(
            "user-1",
            period="weekly",
            window_start=datetime(2026, 5, 25, tzinfo=timezone.utc),
            window_end=datetime(2026, 6, 1, tzinfo=timezone.utc),
            metrics_summary={"tir_pct": 72, "delta_tir_pct": 3, "mean_mgdl": 150},
            now=NOW,
        )
        self.assertIn("TIR", summary.content)
        self.assertIn("72", summary.content)
        self.assertIn("环比+3", summary.content)
        self.assertIn("晚餐后血糖偏高", summary.content)

        latest = self.mem.latest_summary("user-1")
        self.assertEqual(latest.summary_id, summary.summary_id)
        self.assertEqual(latest.metrics["tir_pct"], 72)

    def test_prefetch_injects_warm_summary(self) -> None:
        self._seed_episode()
        self.consolidation.synthesize_state(
            "user-1",
            period="weekly",
            window_start=datetime(2026, 5, 25, tzinfo=timezone.utc),
            window_end=datetime(2026, 6, 1, tzinfo=timezone.utc),
            metrics_summary={"tir_pct": 72},
            now=NOW,
        )
        provider = CGMMemoryProvider(self.store, user_id="user-1")
        provider.initialize(session_id="warm", user_id="user-1")
        recall = provider.prefetch("怎么样")
        self.assertIn("[CGM state summary]", recall)
        self.assertIn("TIR", recall)

    def test_synthesize_state_computes_tir_delta_from_previous_summary(self) -> None:
        self.consolidation.synthesize_state(
            "user-1",
            period="weekly",
            window_start=datetime(2026, 5, 18, tzinfo=timezone.utc),
            window_end=datetime(2026, 5, 25, tzinfo=timezone.utc),
            metrics_summary={"tir_pct": 65},
            now=NOW,
        )

        summary = self.consolidation.synthesize_state(
            "user-1",
            period="weekly",
            window_start=datetime(2026, 5, 25, tzinfo=timezone.utc),
            window_end=datetime(2026, 6, 1, tzinfo=timezone.utc),
            metrics_summary={"tir_pct": 72},
            now=NOW,
        )

        self.assertEqual(summary.metrics["delta_tir_pct"], 7.0)
        self.assertIn("环比+7.0", summary.content)


if __name__ == "__main__":
    unittest.main()
