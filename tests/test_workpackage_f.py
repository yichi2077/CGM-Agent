"""Tests for Work Package F: food recording capability (F1/F2/F3).

F1: structured meal fields in events.create (schema + handler)
F2: MealCorrelationAnalyzer (postprandial response + find_similar_meals)
F3: expanded food keywords in memory provider + candidate summary enrichment
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hermes_cgm_agent.domain import (
    GlucosePoint,
    GlucoseUnit,
    QualityFlag,
    UserEvent,
)
from hermes_cgm_agent.services.analytics.meal_correlation import (
    MealCorrelationAnalyzer,
)
from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.memory.provider import (
    _candidate_summary,
    _looks_memory_relevant,
)
from hermes_cgm_agent.services.reports.narrative_templates import render_meal_summary
from hermes_cgm_agent.services.tools import ToolExecutor, build_default_tool_registry
from hermes_cgm_agent.services.tools.handlers.events import (
    _build_meal_summary,
    _apply_meal_structure,
)
from hermes_cgm_agent.storage.sqlite import SQLiteStore


# ======================================================================
# F1: Structured meal fields — schema
# ======================================================================


class F1SchemaTests(unittest.TestCase):
    """F1: events.create schema advertises food_items and meal_time."""

    def test_events_create_schema_has_food_items(self) -> None:
        registry = build_default_tool_registry()
        event_schema = registry.get("events.create").input_schema["properties"]["event"]
        self.assertIn("food_items", event_schema["properties"])

        food_items_schema = event_schema["properties"]["food_items"]
        self.assertEqual(food_items_schema["type"], "array")
        item_schema = food_items_schema["items"]
        self.assertIn("name", item_schema["properties"])
        self.assertIn("portion", item_schema["properties"])
        self.assertIn("estimated_carbs_g", item_schema["properties"])
        self.assertEqual(set(item_schema["required"]), {"name"})

    def test_events_create_schema_has_meal_time(self) -> None:
        registry = build_default_tool_registry()
        event_schema = registry.get("events.create").input_schema["properties"]["event"]
        self.assertIn("meal_time", event_schema["properties"])

        meal_time_schema = event_schema["properties"]["meal_time"]
        self.assertEqual(meal_time_schema["type"], "string")
        self.assertEqual(
            set(meal_time_schema["enum"]),
            {"breakfast", "lunch", "dinner", "snack"},
        )

    def test_food_fields_are_optional(self) -> None:
        """food_items and meal_time must NOT be in required."""
        registry = build_default_tool_registry()
        event_schema = registry.get("events.create").input_schema["properties"]["event"]
        self.assertNotIn("food_items", event_schema["required"])
        self.assertNotIn("meal_time", event_schema["required"])


# ======================================================================
# F1: Structured meal fields — handler
# ======================================================================


class F1HandlerTests(unittest.TestCase):
    """F1: handler stores structured fields in payload and generates summary."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "app.db"
        self.store = SQLiteStore(db_path)
        self.store.initialize()
        self.repository = SQLiteCGMRepository(self.store)
        self.session_id = "f1-test"
        self.executor = ToolExecutor(
            repository=self.repository,
            audit_service=AuditService(self.store),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_meal_event_with_structured_fields_stores_in_payload(self) -> None:
        response = self.executor.execute(
            tool_name="events.create",
            session_id=self.session_id,
            arguments={
                "user_id": "user-1",
                "event": {
                    "event_type": "meal",
                    "ts_start": "2026-05-31T12:00:00+00:00",
                    "food_items": [
                        {"name": "面条", "portion": "一碗", "estimated_carbs_g": 60},
                        {"name": "水果", "portion": "一份", "estimated_carbs_g": 15},
                    ],
                    "meal_time": "lunch",
                },
            },
        )
        body = response.to_dict()
        self.assertEqual(body["status"], "ok")

        payload = body["event"]["payload"]
        self.assertIn("food_items", payload)
        self.assertEqual(len(payload["food_items"]), 2)
        self.assertEqual(payload["food_items"][0]["name"], "面条")
        self.assertEqual(payload["meal_time"], "lunch")

    def test_meal_event_generates_structured_summary(self) -> None:
        response = self.executor.execute(
            tool_name="events.create",
            session_id=self.session_id,
            arguments={
                "user_id": "user-1",
                "event": {
                    "event_type": "meal",
                    "ts_start": "2026-05-31T12:00:00+00:00",
                    "food_items": [
                        {"name": "面条", "portion": "一碗", "estimated_carbs_g": 60},
                        {"name": "水果", "portion": "一份", "estimated_carbs_g": 15},
                    ],
                    "meal_time": "lunch",
                },
            },
        )
        body = response.to_dict()
        payload = body["event"]["payload"]
        self.assertIn("structured_summary", payload)
        summary = payload["structured_summary"]
        self.assertIn("午餐", summary)
        self.assertIn("面条", summary)
        self.assertIn("水果", summary)
        self.assertIn("碳水", summary)

    def test_structured_meal_narrative_includes_food_names(self) -> None:
        response = self.executor.execute(
            tool_name="events.create",
            session_id=self.session_id,
            arguments={
                "user_id": "user-1",
                "event": {
                    "event_type": "meal",
                    "ts_start": "2026-05-31T12:00:00+00:00",
                    "food_items": [{"name": "面条"}, {"name": "水果"}],
                },
            },
        )
        event = UserEvent.model_validate(response.to_dict()["event"])
        narrative = render_meal_summary(event)
        self.assertIn("面条", narrative)
        self.assertIn("水果", narrative)

    def test_legacy_type_alias_generates_structured_summary(self) -> None:
        response = self.executor.execute(
            tool_name="events.create",
            session_id=self.session_id,
            arguments={
                "user_id": "user-1",
                "event": {
                    "type": "meal",
                    "ts_start": "2026-05-31T12:00:00+00:00",
                    "food_items": [{"name": "面条"}],
                    "meal_time": "lunch",
                },
            },
        )
        payload = response.to_dict()["event"]["payload"]
        self.assertIn("structured_summary", payload)
        self.assertIn("面条", payload["structured_summary"])

    def test_meal_event_without_food_items_still_works(self) -> None:
        """A free-text meal event (no food_items) should still work."""
        response = self.executor.execute(
            tool_name="events.create",
            session_id=self.session_id,
            arguments={
                "user_id": "user-1",
                "event": {
                    "event_type": "meal",
                    "ts_start": "2026-05-31T12:00:00+00:00",
                    "payload": {"description": "随便吃了点"},
                },
            },
        )
        body = response.to_dict()
        self.assertEqual(body["status"], "ok")
        # No structured_summary should be generated for free-text meals
        self.assertNotIn("structured_summary", body["event"]["payload"])

    def test_non_meal_event_ignores_food_fields(self) -> None:
        """food_items/meal_time on a non-meal event should be folded as extras."""
        response = self.executor.execute(
            tool_name="events.create",
            session_id=self.session_id,
            arguments={
                "user_id": "user-1",
                "event": {
                    "event_type": "exercise",
                    "ts_start": "2026-05-31T18:00:00+00:00",
                    "food_items": [{"name": "should be ignored"}],
                },
            },
        )
        body = response.to_dict()
        self.assertEqual(body["status"], "ok")
        # food_items should be folded into payload as an extra, no summary
        payload = body["event"]["payload"]
        self.assertIn("food_items", payload)
        self.assertNotIn("structured_summary", payload)

    def test_food_items_in_payload_are_preserved(self) -> None:
        """When food_items are nested inside payload, they are preserved."""
        response = self.executor.execute(
            tool_name="events.create",
            session_id=self.session_id,
            arguments={
                "user_id": "user-1",
                "event": {
                    "event_type": "meal",
                    "ts_start": "2026-05-31T08:00:00+00:00",
                    "payload": {
                        "food_items": [{"name": "面包", "portion": "两片"}],
                        "meal_time": "breakfast",
                    },
                },
            },
        )
        body = response.to_dict()
        payload = body["event"]["payload"]
        self.assertIn("food_items", payload)
        self.assertEqual(payload["food_items"][0]["name"], "面包")
        self.assertIn("structured_summary", payload)
        self.assertIn("早餐", payload["structured_summary"])
        self.assertIn("面包", payload["structured_summary"])

    def test_build_meal_summary_with_carbs(self) -> None:
        summary = _build_meal_summary(
            [
                {"name": "面条", "portion": "一碗", "estimated_carbs_g": 60},
                {"name": "水果", "portion": "一份", "estimated_carbs_g": 15},
            ],
            "lunch",
        )
        self.assertIn("午餐", summary)
        self.assertIn("面条（一碗）", summary)
        self.assertIn("水果（一份）", summary)
        self.assertIn("75g", summary)

    def test_build_meal_summary_without_carbs(self) -> None:
        summary = _build_meal_summary(
            [{"name": "沙拉", "portion": "一盘"}],
            "dinner",
        )
        self.assertIn("晚餐", summary)
        self.assertIn("沙拉（一盘）", summary)
        self.assertNotIn("碳水", summary)

    def test_build_meal_summary_no_food_items(self) -> None:
        summary = _build_meal_summary(None, "snack")
        self.assertEqual(summary, "加餐")

    def test_build_meal_summary_empty_list(self) -> None:
        summary = _build_meal_summary([], "breakfast")
        self.assertEqual(summary, "早餐")

    def test_apply_meal_structure_in_place(self) -> None:
        """Unit test: _apply_meal_structure moves fields into payload."""
        event_raw = {
            "event_type": "meal",
            "ts_start": "2026-05-31T12:00:00+00:00",
            "food_items": [{"name": "米饭", "portion": "一碗"}],
            "meal_time": "lunch",
        }
        _apply_meal_structure(event_raw)
        # food_items and meal_time should be moved to payload
        self.assertNotIn("food_items", event_raw)
        self.assertNotIn("meal_time", event_raw)
        self.assertIn("food_items", event_raw["payload"])
        self.assertIn("meal_time", event_raw["payload"])
        self.assertIn("structured_summary", event_raw["payload"])

    def test_meal_correlation_tool_exposes_confirmed_history(self) -> None:
        event = UserEvent(
            event_id="confirmed-meal",
            user_id="user-1",
            type="meal",
            ts_start=datetime.now(timezone.utc) - timedelta(minutes=30),
            payload={"food_items": [{"name": "面条"}]},
            created_by="user",
            user_confirmed=True,
        )
        self.repository.create_user_event(event)
        response = self.executor.execute(
            tool_name="meals.find_similar",
            session_id=self.session_id,
            arguments={"user_id": "user-1", "food_name": "面条"},
        ).to_dict()
        self.assertEqual(response["status"], "ok")
        self.assertEqual(len(response["matches"]), 1)
        self.assertEqual(response["matches"][0]["matched_food_name"], "面条")


# ======================================================================
# F2: MealCorrelationAnalyzer — analyze_response
# ======================================================================


class F2AnalyzeResponseTests(unittest.TestCase):
    """F2: MealCorrelationAnalyzer.analyze_response computes peak, time, AUC."""

    def setUp(self) -> None:
        self.analyzer = MealCorrelationAnalyzer(window_hours=3)
        self.meal_start = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)

    def _make_event(self) -> UserEvent:
        return UserEvent(
            event_id="meal-1",
            user_id="user-1",
            type="meal",
            ts_start=self.meal_start,
            payload={"food_items": [{"name": "面条"}], "meal_time": "lunch"},
            created_by="agent",
            user_confirmed=False,
        )

    def _make_point(
        self,
        minutes_after_meal: float,
        value: float,
    ) -> GlucosePoint:
        return GlucosePoint(
            user_id="user-1",
            timestamp=self.meal_start + timedelta(minutes=minutes_after_meal),
            value=value,
            unit=GlucoseUnit.MG_DL,
            source="test",
            quality_flag=QualityFlag.VALID,
        )

    def test_response_with_rising_glucose(self) -> None:
        """Glucose rises from 100 to 180 over 2 hours."""
        meal = self._make_event()
        points = [
            self._make_point(-10, 100),   # pre-meal baseline
            self._make_point(0, 105),     # at meal start — becomes baseline
            self._make_point(30, 130),
            self._make_point(60, 160),
            self._make_point(90, 180),    # peak
            self._make_point(120, 170),
            self._make_point(150, 140),
        ]
        response = self.analyzer.analyze_response(meal, points)

        # Baseline is the last point at or before meal start (the 105 at t=0)
        self.assertEqual(response.point_count, 6)  # excludes pre-meal point at -10
        self.assertEqual(response.peak_value_mg_dl, 180.0)
        self.assertEqual(response.peak_time_minutes, 90.0)
        self.assertEqual(response.baseline_value_mg_dl, 105.0)
        self.assertEqual(response.delta_peak_mg_dl, 75.0)
        self.assertIsNotNone(response.auc_mg_dl_min)
        self.assertGreater(response.auc_mg_dl_min, 0)

    def test_response_no_points_in_window(self) -> None:
        """No glucose points in the postprandial window."""
        meal = self._make_event()
        points = [
            self._make_point(-60, 100),  # only pre-meal, far before
        ]
        response = self.analyzer.analyze_response(meal, points)

        self.assertEqual(response.point_count, 0)
        self.assertIsNone(response.peak_value_mg_dl)
        self.assertIsNone(response.auc_mg_dl_min)
        self.assertEqual(response.window_minutes, 0.0)

    def test_response_single_point(self) -> None:
        """Only one postprandial point — AUC should be None."""
        meal = self._make_event()
        points = [
            self._make_point(-5, 100),
            self._make_point(30, 140),
        ]
        response = self.analyzer.analyze_response(meal, points)

        self.assertEqual(response.point_count, 1)
        self.assertEqual(response.peak_value_mg_dl, 140.0)
        self.assertEqual(response.baseline_value_mg_dl, 100.0)
        # Only one point → can't compute AUC
        self.assertIsNone(response.auc_mg_dl_min)

    def test_response_does_not_use_day_old_baseline(self) -> None:
        meal = self._make_event()
        points = [
            self._make_point(-24 * 60, 90),
            self._make_point(30, 150),
        ]
        response = self.analyzer.analyze_response(meal, points)
        self.assertIsNone(response.baseline_value_mg_dl)
        self.assertIsNone(response.delta_peak_mg_dl)
        self.assertIsNone(response.auc_mg_dl_min)

    def test_response_respects_window_hours(self) -> None:
        """Points beyond window_hours are excluded."""
        meal = self._make_event()
        points = [
            self._make_point(-5, 100),
            self._make_point(30, 130),
            self._make_point(60, 160),
            self._make_point(180, 200),   # exactly at 3h boundary — excluded (<)
            self._make_point(200, 210),   # beyond window
        ]
        response = self.analyzer.analyze_response(meal, points, window_hours=3)
        self.assertEqual(response.point_count, 2)

    def test_response_window_override(self) -> None:
        """window_hours parameter overrides default."""
        meal = self._make_event()
        points = [
            self._make_point(-5, 100),
            self._make_point(30, 130),
            self._make_point(60, 160),
            self._make_point(90, 180),
            self._make_point(120, 170),
        ]
        response_2h = self.analyzer.analyze_response(meal, points, window_hours=2)
        # Window [0, 120) min → 30, 60, 90 (120 is excluded by <)
        self.assertEqual(response_2h.point_count, 3)

    def test_response_filters_by_user_id(self) -> None:
        """Points from other users are excluded."""
        meal = self._make_event()
        other_point = GlucosePoint(
            user_id="user-2",
            timestamp=self.meal_start + timedelta(minutes=30),
            value=200,
            unit=GlucoseUnit.MG_DL,
            source="test",
            quality_flag=QualityFlag.VALID,
        )
        own_point = self._make_point(30, 130)
        response = self.analyzer.analyze_response(meal, [other_point, own_point])
        self.assertEqual(response.point_count, 1)


# ======================================================================
# F2: MealCorrelationAnalyzer — find_similar_meals
# ======================================================================


class F2FindSimilarMealsTests(unittest.TestCase):
    """F2: find_similar_meals searches historical meals by food name."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "app.db"
        self.store = SQLiteStore(db_path)
        self.store.initialize()
        self.repository = SQLiteCGMRepository(self.store)
        self.analyzer = MealCorrelationAnalyzer(window_hours=3)
        self.reference_time = datetime.now(timezone.utc).replace(microsecond=0)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _create_meal_event(
        self,
        user_id: str,
        ts_start: datetime,
        food_items: list[dict],
        meal_time: str = "lunch",
        user_confirmed: bool = True,
    ) -> str:
        from hermes_cgm_agent.services.tools.handlers.events import _apply_meal_structure
        import uuid

        event_raw = {
            "event_id": uuid.uuid4().hex,
            "user_id": user_id,
            "event_type": "meal",
            "ts_start": ts_start,
            "created_by": "agent",
            "user_confirmed": user_confirmed,
            "food_items": food_items,
            "meal_time": meal_time,
        }
        _apply_meal_structure(event_raw)
        event = UserEvent.model_validate(event_raw)
        return self.repository.create_user_event(event)

    def _create_glucose_points(
        self,
        user_id: str,
        start: datetime,
        values: list[tuple[float, float]],
    ) -> None:
        """Create glucose points: list of (minutes_offset, value_mg_dl)."""
        for minutes, value in values:
            self.repository.create_glucose_point(
                GlucosePoint(
                    user_id=user_id,
                    timestamp=start + timedelta(minutes=minutes),
                    value=value,
                    unit=GlucoseUnit.MG_DL,
                    source="test",
                    quality_flag=QualityFlag.VALID,
                )
            )

    def test_find_similar_meals_by_food_name(self) -> None:
        """Search for '面条' finds meals containing that food."""
        meal_time = self.reference_time - timedelta(days=2)
        self._create_meal_event(
            "user-1", meal_time,
            [{"name": "面条", "portion": "一碗", "estimated_carbs_g": 60}],
        )
        self._create_glucose_points("user-1", meal_time, [
            (-5, 100), (30, 130), (60, 160), (90, 180), (120, 150),
        ])

        # Also create a non-matching meal
        self._create_meal_event(
            "user-1", self.reference_time - timedelta(days=3),
            [{"name": "米饭", "portion": "一碗"}],
        )

        results = self.analyzer.find_similar_meals("面条", "user-1", self.repository)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].matched_food_name, "面条")
        self.assertIsNotNone(results[0].response)
        self.assertEqual(results[0].response.peak_value_mg_dl, 180.0)

    def test_find_similar_meals_no_match(self) -> None:
        """Search for a food not in any meal returns empty list."""
        meal_time = self.reference_time - timedelta(days=2)
        self._create_meal_event(
            "user-1", meal_time,
            [{"name": "面条", "portion": "一碗"}],
        )

        results = self.analyzer.find_similar_meals("披萨", "user-1", self.repository)
        self.assertEqual(len(results), 0)

    def test_find_similar_meals_excludes_unconfirmed_agent_candidates(self) -> None:
        self._create_meal_event(
            "user-1",
            self.reference_time - timedelta(days=1),
            [{"name": "面条"}],
            user_confirmed=False,
        )
        results = self.analyzer.find_similar_meals("面条", "user-1", self.repository)
        self.assertEqual(results, [])

    def test_find_similar_meals_does_not_match_english_substrings(self) -> None:
        event = UserEvent(
            event_id="price-note",
            user_id="user-1",
            type="meal",
            ts_start=self.reference_time - timedelta(days=1),
            payload={"description": "We discussed the price of the project."},
            created_by="user",
            user_confirmed=True,
        )
        self.repository.create_user_event(event)
        self.assertEqual(
            self.analyzer.find_similar_meals("rice", "user-1", self.repository),
            [],
        )

    def test_find_similar_meals_matches_structured_summary(self) -> None:
        """Search also matches food names in structured_summary."""
        meal_time = self.reference_time - timedelta(days=2)
        self._create_meal_event(
            "user-1", meal_time,
            [{"name": "饺子", "portion": "十个"}],
        )
        results = self.analyzer.find_similar_meals("饺子", "user-1", self.repository)
        self.assertEqual(len(results), 1)

    def test_find_similar_meals_matches_freeform_payload(self) -> None:
        """Search also matches food names in freeform payload text."""
        import uuid

        event = UserEvent(
            event_id=uuid.uuid4().hex,
            user_id="user-1",
            type="meal",
            ts_start=self.reference_time - timedelta(days=2),
            payload={"description": "今天中午吃了煎饼"},
            created_by="agent",
            user_confirmed=True,
        )
        self.repository.create_user_event(event)

        results = self.analyzer.find_similar_meals("煎饼", "user-1", self.repository)
        self.assertEqual(len(results), 1)

    def test_find_similar_meals_sorted_by_recency(self) -> None:
        """Results are sorted most-recent first."""
        old_time = self.reference_time - timedelta(days=30)
        new_time = self.reference_time - timedelta(days=2)
        self._create_meal_event(
            "user-1", old_time,
            [{"name": "面条", "portion": "一碗"}],
        )
        self._create_meal_event(
            "user-1", new_time,
            [{"name": "面条", "portion": "两碗"}],
        )

        results = self.analyzer.find_similar_meals("面条", "user-1", self.repository)
        self.assertEqual(len(results), 2)
        self.assertGreater(results[0].event.ts_start, results[1].event.ts_start)

    def test_find_similar_meals_limit(self) -> None:
        """limit parameter controls result count."""
        for day in range(1, 6):
            self._create_meal_event(
                "user-1",
                self.reference_time - timedelta(days=day),
                [{"name": "面条", "portion": "一碗"}],
            )

        results = self.analyzer.find_similar_meals(
            "面条", "user-1", self.repository, limit=3
        )
        self.assertEqual(len(results), 3)


# ======================================================================
# F3: Expanded food keywords in _looks_memory_relevant
# ======================================================================


class F3ExpandedKeywordsTests(unittest.TestCase):
    """F3: _looks_memory_relevant recognizes expanded food vocabulary."""

    def test_chinese_food_terms_recognized(self) -> None:
        samples = [
            "今天中午吃了饺子和包子",
            "早上喝了豆浆配油条",
            "晚上吃了麻辣烫",
            "下午吃了巧克力和冰淇淋",
            "最近天天喝可乐",
            "昨晚吃了烧烤和串串",
            "早餐吃了燕麦和牛奶",
            "没吃早饭，有点饿",
            "今天碳水吃多了",
            "下午馋了，吃了一包薯片",
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertTrue(
                    _looks_memory_relevant(sample),
                    f"Expected memory-relevant: {sample}",
                )

    def test_english_food_terms_recognized(self) -> None:
        samples = [
            "I had pizza for dinner",
            "ate a burger and fries",
            "drank coffee this morning",
            "had some chocolate cookies",
            "made a salad for lunch",
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertTrue(
                    _looks_memory_relevant(sample),
                    f"Expected memory-relevant: {sample}",
                )

    def test_non_food_text_still_not_relevant(self) -> None:
        """Common non-health text should not trigger memory relevance."""
        # Note: "low" and "high" are in the keyword list, so we avoid those.
        samples = [
            "今天天气不错",
            "The sky is blue today",
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertFalse(_looks_memory_relevant(sample))

    def test_existing_keywords_still_work(self) -> None:
        """Pre-existing keywords must not be broken by the expansion."""
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


# ======================================================================
# F3: Candidate summary food name preservation
# ======================================================================


class F3CandidateSummaryTests(unittest.TestCase):
    """F3: _candidate_summary preserves food names past truncation point."""

    def test_short_text_unchanged(self) -> None:
        text = "中午吃了面条"
        self.assertEqual(_candidate_summary(text), text)

    def test_long_text_truncates_with_ellipsis(self) -> None:
        text = "今天天气很好，出去散步了，" + "x" * 200
        result = _candidate_summary(text)
        self.assertTrue(result.endswith("..."))
        self.assertLessEqual(len(result), 200)

    def test_food_name_preserved_past_truncation(self) -> None:
        """A food name appearing after the truncation point is appended."""
        # Build text where 饺子 appears well past char 180
        text = "今天整体感觉还不错，血糖也比较平稳，" + "然后又聊了一些家常话，" * 20 + "最后下午吃了饺子"
        result = _candidate_summary(text)
        self.assertIn("饺子", result)
        # The food name should be in the appended section
        self.assertIn("食物:", result)

    def test_multiple_food_names_preserved(self) -> None:
        """Multiple food names past truncation are all appended."""
        text = (
            "今天整体感觉还不错，" + "聊天记录比较多，" * 15
            + "最后吃了饺子和面条还有火锅"
        )
        result = _candidate_summary(text)
        self.assertIn("饺子", result)
        self.assertIn("面条", result)
        self.assertIn("火锅", result)

    def test_food_name_already_in_truncated_part_not_duplicated(self) -> None:
        """If food name is already in the truncated text, it's not re-appended."""
        text = "中午吃了面条，" + "x" * 200 + "然后又吃了面条"
        result = _candidate_summary(text)
        # 面条 should appear at most once in the 食物: section
        food_section = result.split("食物:")[-1] if "食物:" in result else ""
        self.assertLessEqual(food_section.count("面条"), 1)

    def test_ascii_food_names_do_not_match_normal_words(self) -> None:
        text = "x" * 190 + " We discussed the team price during the meeting."
        result = _candidate_summary(text)
        self.assertNotIn("食物:rice", result)
        self.assertNotIn("tea", result)

    def test_food_name_straddling_truncation_is_preserved(self) -> None:
        text = "x" * 176 + "火锅" + "z" * 10
        result = _candidate_summary(text)
        self.assertIn("火锅", result)
        self.assertIn("食物:", result)

    def test_no_food_name_no_appended_section(self) -> None:
        """Long text without food names doesn't get a 食物: section."""
        text = "今天天气很好，出去散步了，" + "感觉还不错，" * 30
        result = _candidate_summary(text)
        self.assertNotIn("食物:", result)


if __name__ == "__main__":
    unittest.main()
