"""F4 companion narrative templates, metric translation, and text validation.

Includes:
- Conversational Chinese templates for L3 Hypothesis states.
- Translation of clinical metrics into life language based on audience.
- Strict validation check to ensure no clinical jargon leaks to F4 companion tone.
"""

from __future__ import annotations

import re
from typing import Any

# Clinical abbreviations forbidden in F4 companion output (CHK008 / FR-005).
_BLACKLIST_ABBRS: tuple[str, ...] = ("TIR", "TAR", "TBR", "GMI", "CV", "LBGI", "HBGI")
# Assertive/causal phrases forbidden by the Informed-Companion persona (Principle IV).
_BLACKLIST_PHRASES: tuple[str, ...] = (
    "经分析发现", "研究表明", "数据证明", "可以确定", "证明了", "明显表明", "确实是", "绝对",
)
# Match an abbreviation only as a standalone ASCII token (not embedded in a larger
# latin word like CGM/RECV), regardless of adjacent CJK characters. Fixes the
# substring false-positive where bare ``"CV" in text`` flagged any latin "cv".
_ABBR_PATTERNS: dict[str, re.Pattern[str]] = {
    abbr: re.compile(rf"(?<![A-Za-z]){re.escape(abbr)}(?![A-Za-z])")
    for abbr in _BLACKLIST_ABBRS
}


def check_companion_text(text: str, max_len: int = 80) -> list[str]:
    """Return a list of violation tags (empty == clean). Pure; never raises.

    Tags: ``abbr:<X>`` (clinical abbreviation), ``phrase:<X>`` (assertive/causal),
    ``length:<n>>max`` (over length). Callers decide how to react per tag type.
    """
    violations: list[str] = []
    upper = text.upper()
    for abbr, pattern in _ABBR_PATTERNS.items():
        if pattern.search(upper):
            violations.append(f"abbr:{abbr}")
    for phrase in _BLACKLIST_PHRASES:
        if phrase in text:
            violations.append(f"phrase:{phrase}")
    if len(text) > max_len:
        violations.append(f"length:{len(text)}>{max_len}")
    return violations


def validate_companion_text(text: str, max_len: int = 80) -> bool:
    """Strict validator (test/guard layer): raises ValueError on ANY violation.

    Forbids clinical abbreviations, assertive/causal phrases, and over-length.
    Used as the hard guard in tests to protect the templates themselves.
    """
    upper = text.upper()
    for abbr, pattern in _ABBR_PATTERNS.items():
        if pattern.search(upper):
            raise ValueError(f"Clinical abbreviation '{abbr}' is forbidden in companion narratives.")
    for phrase in _BLACKLIST_PHRASES:
        if phrase in text:
            raise ValueError(f"Assertive/causal phrase '{phrase}' is forbidden in companion narratives.")
    if len(text) > max_len:
        raise ValueError(f"Text length ({len(text)}) exceeds the maximum allowed length of {max_len} characters.")
    return True


def enforce_companion_text(text: str, max_len: int = 80) -> str:
    """Runtime guard (N4 split): blacklist is a hard gate, length degrades gracefully.

    - Clinical abbreviation / assertive phrase -> **raise** (Principle IV hard gate;
      such content must never reach the user as companion narrative).
    - Over-length -> **truncate** with an ellipsis and return, so an over-long card
      never crashes report/push generation (FR-013: narrative is a rendering concern).
    """
    upper = text.upper()
    for abbr, pattern in _ABBR_PATTERNS.items():
        if pattern.search(upper):
            raise ValueError(f"Clinical abbreviation '{abbr}' is forbidden in companion narratives.")
    for phrase in _BLACKLIST_PHRASES:
        if phrase in text:
            raise ValueError(f"Assertive/causal phrase '{phrase}' is forbidden in companion narratives.")
    if len(text) > max_len:
        return text[: max(0, max_len - 1)].rstrip() + "…"
    return text


_BEHAVIOR_MAP = {
    "post lunch spike": "午餐后血糖偏高",
    "post breakfast spike": "早餐后血糖偏高",
    "post dinner spike": "晚餐后血糖偏高",
    "overnight low": "夜间低血糖",
    "fasting high": "空腹血糖偏高",
    "hypo": "偏低片段",
    "hyper": "偏高片段",
    "rapid rise": "上冲片段",
    "rapid_rise": "上冲片段",
    "rapid fall": "回落片段",
    "rapid_fall": "回落片段",
    "overnight_low": "夜间偏低片段",
}


def describe_behavior(statement: str) -> str:
    """Map an internal hypothesis statement to natural Chinese life-language.

    Shared by the report narrative AND the warm state digest (D052): the
    digest previously injected raw statements like "Recurring rapid rise
    pattern" into the conversation context, leaking English tech jargon into
    the companion tone.
    """
    behavior = statement
    for prefix in ["Recurring ", "recurring "]:
        if behavior.startswith(prefix):
            behavior = behavior[len(prefix):]
    for suffix in [" pattern", " Pattern"]:
        if behavior.endswith(suffix):
            behavior = behavior[:-len(suffix)]
    return _BEHAVIOR_MAP.get(behavior.lower(), behavior)


def render_episode_summary(event: Any) -> str:
    """Render a detected glucose event as Chinese life-language for memory (D058).

    The detector writes an English clinical summary ("Low glucose episode:
    nadir 48.2 mg/dL for 40 min.") that is the correct raw/audit form on the
    GlucoseEvent. But the L1 EPISODE derived from it is recalled into every
    conversation — feeding the companion English mg/dL strings (while the user
    reads mmol/L) is the number→language failure at the most-used surface.
    This renders the same fact in the user's voice and display unit; the raw
    English summary stays on the event for clinician/audit paths.
    """
    from hermes_cgm_agent.config import display_glucose_unit
    from hermes_cgm_agent.domain import GlucoseUnit, convert_glucose_value

    etype = getattr(event.event_type, "value", event.event_type)
    unit = display_glucose_unit()

    def _fmt(value_mgdl: float | None) -> str | None:
        if value_mgdl is None:
            return None
        if unit == "mmol/L":
            return f"{round(convert_glucose_value(float(value_mgdl), GlucoseUnit.MG_DL, GlucoseUnit.MMOL_L), 1)} mmol/L"
        return f"{round(float(value_mgdl), 1)} mg/dL"

    nadir = _fmt(getattr(event, "nadir_value_mg_dl", None))
    peak = _fmt(getattr(event, "peak_value_mg_dl", None))
    minutes = round(getattr(event, "duration_minutes", 0) or 0)
    # Time-of-day anchor gives the user useful "when" context AND keeps
    # otherwise-generic events (rapid rise/fall) distinct in recall.
    when = _time_of_day_label(getattr(event, "ts_start", None))

    if etype == "overnight_low":
        tail = f"，最低到 {nadir}" if nadir else ""
        return f"夜里有一段血糖偏低{tail}，持续了大约 {minutes} 分钟。"
    if etype == "hypo":
        tail = f"，最低到 {nadir}" if nadir else ""
        return f"{when}有一段血糖偏低{tail}，持续了大约 {minutes} 分钟。"
    if etype == "hyper":
        tail = f"，最高到 {peak}" if peak else ""
        return f"{when}有一段血糖偏高{tail}，持续了大约 {minutes} 分钟。"
    if etype == "rapid_rise":
        return f"{when}血糖上冲得比较快。"
    if etype == "rapid_fall":
        return f"{when}血糖回落得比较快。"
    # Fallback: never leak the raw English summary; describe by type.
    return f"{when}记录到一段{describe_behavior(str(etype))}。"


def _time_of_day_label(ts: Any) -> str:
    """Life-language time-of-day for an event (local Asia/Shanghai)."""
    if ts is None:
        return "有一次"
    try:
        from zoneinfo import ZoneInfo

        hour = ts.astimezone(ZoneInfo("Asia/Shanghai")).hour
    except Exception:
        return "有一次"
    if 5 <= hour < 9:
        return "早上"
    if 9 <= hour < 11:
        return "上午"
    if 11 <= hour < 13:
        return "中午"
    if 13 <= hour < 17:
        return "下午"
    if 17 <= hour < 20:
        return "傍晚"
    if 20 <= hour < 23:
        return "晚上"
    return "夜里"


def render_hypothesis_narrative(state: str, statement: str, evidence_count: int = 0) -> str:
    """Format L3 Hypothesis narrative using协商式 style based on state."""
    behavior_cn = describe_behavior(statement)

    state_str = getattr(state, "value", state).lower()
    if state_str == "candidate":
        return f"看起来可能和{behavior_cn}有关，你觉得可能是因为这个吗？要不要接下来多留意一下？"
    elif state_str == "observing":
        if evidence_count <= 0:
            # Defensive (spec Edge Case): OBSERVING with no evidence should read
            # like a fresh candidate, not claim "0 times". Must not crash.
            return f"看起来可能和{behavior_cn}有关，你觉得可能是因为这个吗？要不要接下来多留意一下？"
        return f"在过去几天的记录中，有{evidence_count}次类似于{behavior_cn}的情况。我们再观察看看是不是这个规律？"
    elif state_str == "stable":
        return f"在你的记录中，{behavior_cn}这个模式比较常见，这可能是一个比较固定的规律了。"
    elif state_str == "archived":
        return f"之前关于{behavior_cn}的规律最近不明显了，我们先把它放一边吧。"
    else:
        return f"关于{behavior_cn}的情况，我们再一起观察看看。"


def translate_metric(name: str, value: float | None, audience: str) -> str:
    """Translate raw clinical metrics into natural Chinese life-language for SELF/FAMILY."""
    if value is None:
        return ""
    
    audience_str = getattr(audience, "value", audience).upper()
    name_upper = name.upper()
    
    if audience_str == "CLINICIAN":
        # Keep raw/clinical format for clinician audience
        return f"{name_upper} {value}"
        
    if name_upper == "TIR":
        if audience_str == "FAMILY":
            return "大部分时间都挺好" if value >= 70.0 else "有一些时间波动"
        if value >= 95.0:
            return "几乎所有时间都在目标范围内"
        elif value >= 70.0:
            return "大部分时间都在范围里"
        elif value >= 50.0:
            return "有一半以上的时间在范围里"
        else:
            return "在范围里的时间较少"
            
    elif name_upper == "TAR":
        if value == 0:
            return "没有偏高"
        return "偏高的时候"
        
    elif name_upper == "TBR":
        if value == 0:
            return "没有偏低"
        return "偏低的时候"
        
    elif name_upper == "MBG":
        return "平均状态" if audience_str == "FAMILY" else "平均血糖水平"
        
    elif name_upper == "CV":
        return "血糖起伏" if audience_str == "FAMILY" else "血糖波动情况"
        
    elif name_upper == "GMI":
        return "大体水平" if audience_str == "FAMILY" else "估算糖化血红蛋白"
        
    return str(value)
