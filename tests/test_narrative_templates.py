"""Tests for F4 narrative templates, translation, and validation logic."""

from __future__ import annotations

import unittest

from hermes_cgm_agent.services.reports.narrative_templates import (
    validate_companion_text,
    check_companion_text,
    enforce_companion_text,
    render_hypothesis_narrative,
    translate_metric,
)


class NarrativeTemplatesTests(unittest.TestCase):
    def test_validate_companion_text_valid(self) -> None:
        text = "看起来我们发现午餐后有个小高峰，你觉得是跟下午的加餐有关吗？"
        self.assertTrue(validate_companion_text(text, max_len=80))

    def test_validate_companion_text_blocks_abbreviations(self) -> None:
        # Blocks forbidden clinical abbreviations
        for abbr in ["TIR", "TAR", "TBR", "GMI", "CV", "LBGI", "HBGI"]:
            text = f"今天的 {abbr} 偏低"
            with self.assertRaisesRegex(ValueError, f"Clinical abbreviation '{abbr}' is forbidden"):
                validate_companion_text(text)

    def test_validate_companion_text_blocks_assertive_phrases(self) -> None:
        # Blocks forbidden assertive/causal phrases
        for phrase in ["经分析发现", "研究表明", "数据证明", "可以确定", "绝对"]:
            text = f"这{phrase}是好习惯"
            with self.assertRaisesRegex(ValueError, f"phrase '{phrase}' is forbidden"):
                validate_companion_text(text)

    def test_validate_companion_text_enforces_length_limits(self) -> None:
        # Enforces length constraint
        text = "x" * 51
        with self.assertRaisesRegex(ValueError, "exceeds the maximum allowed length of 50"):
            validate_companion_text(text, max_len=50)

    def test_render_hypothesis_narrative_states(self) -> None:
        # Candidate
        res = render_hypothesis_narrative("candidate", "Recurring post lunch spike pattern")
        self.assertIn("看起来可能和午餐后血糖偏高有关", res)
        self.assertIn("你觉得可能是因为这个吗", res)

        # Observing
        res = render_hypothesis_narrative("observing", "Recurring overnight low pattern", evidence_count=3)
        self.assertIn("在过去几天的记录中，有3次类似于夜间低血糖的情况", res)
        self.assertIn("我们再观察看看", res)

        # Stable
        res = render_hypothesis_narrative("stable", "fasting high pattern")
        self.assertIn("在你的记录中，空腹血糖偏高这个模式比较常见", res)

        # Archived
        res = render_hypothesis_narrative("archived", "post dinner spike")
        self.assertIn("之前关于晚餐后血糖偏高的规律最近不明显了", res)

    def test_translate_metric_self_audience(self) -> None:
        # TIR translations
        self.assertEqual(translate_metric("TIR", 98.0, "SELF"), "几乎所有时间都在目标范围内")
        self.assertEqual(translate_metric("TIR", 85.0, "SELF"), "大部分时间都在范围里")
        self.assertEqual(translate_metric("TIR", 55.0, "SELF"), "有一半以上的时间在范围里")
        self.assertEqual(translate_metric("TIR", 30.0, "SELF"), "在范围里的时间较少")

        # TAR/TBR translations
        self.assertEqual(translate_metric("TAR", 15.0, "SELF"), "偏高的时候")
        self.assertEqual(translate_metric("TAR", 0.0, "SELF"), "没有偏高")
        self.assertEqual(translate_metric("TBR", 5.0, "SELF"), "偏低的时候")
        self.assertEqual(translate_metric("TBR", 0.0, "SELF"), "没有偏低")

        # Other metrics
        self.assertEqual(translate_metric("MBG", 120.0, "SELF"), "平均血糖水平")
        self.assertEqual(translate_metric("CV", 32.0, "SELF"), "血糖波动情况")
        self.assertEqual(translate_metric("GMI", 6.2, "SELF"), "估算糖化血红蛋白")

    def test_translate_metric_family_audience(self) -> None:
        self.assertEqual(translate_metric("TIR", 75.0, "FAMILY"), "大部分时间都挺好")
        self.assertEqual(translate_metric("TIR", 60.0, "FAMILY"), "有一些时间波动")
        self.assertEqual(translate_metric("MBG", 120.0, "FAMILY"), "平均状态")
        self.assertEqual(translate_metric("CV", 32.0, "FAMILY"), "血糖起伏")
        self.assertEqual(translate_metric("GMI", 6.2, "FAMILY"), "大体水平")


class CompanionTextGuardTests(unittest.TestCase):
    """R040: check_/enforce_ split + CV word-boundary fix (N4, N12)."""

    def test_check_returns_violation_tags(self) -> None:
        self.assertEqual(check_companion_text("看起来今天还不错"), [])
        self.assertIn("abbr:TIR", check_companion_text("今天的 TIR 偏低"))
        self.assertIn("phrase:研究表明", check_companion_text("研究表明这样好"))
        self.assertIn("length:51>50", check_companion_text("x" * 51, max_len=50))

    def test_cv_word_boundary_no_false_positive(self) -> None:
        # "CV" embedded in a larger latin token must NOT trip the guard...
        self.assertEqual(check_companion_text("今天用了 CGM 设备"), [])
        self.assertEqual(check_companion_text("RECV buffer 没问题"), [])
        # ...but a standalone CV (even adjacent to CJK) must be flagged.
        self.assertIn("abbr:CV", check_companion_text("今天CV偏高"))
        self.assertTrue(validate_companion_text("今天用了 CGM 设备"))

    def test_enforce_blocks_blacklist_but_truncates_length(self) -> None:
        # Blacklist -> hard raise
        with self.assertRaises(ValueError):
            enforce_companion_text("今天的 TIR 偏低")
        with self.assertRaises(ValueError):
            enforce_companion_text("数据证明这样好")
        # Over-length -> graceful truncation (no raise), result within limit
        out = enforce_companion_text("好" * 200, max_len=50)
        self.assertLessEqual(len(out), 50)
        self.assertTrue(out.endswith("…"))
        # Clean text passes through unchanged
        self.assertEqual(enforce_companion_text("今天整体还行"), "今天整体还行")


if __name__ == "__main__":
    unittest.main()
