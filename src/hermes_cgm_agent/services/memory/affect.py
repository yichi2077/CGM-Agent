"""P1-5 (MVP audit): deterministic emotion detection for emotional-first orchestration.

SOUL.md promises "先回应情绪，再看数据" (emotional-first). Before this module
that promise lived only in the system prompt — 100% dependent on the LLM
honouring it, unobservable and untestable. This module sinks the detection
into code so the orchestration layer (provider.prefetch, report builder) can
make deterministic decisions: inject an empathy anchor, reduce data-injection
strength, and lead reports with acknowledgement instead of numbers.

Demo-grade by design: keyword/rule matching, no model call (per the audit
ruling). The keyword list is the same vocabulary the memory-relevance
detector already recognises, plus common distress phrasing.
"""

from __future__ import annotations

# Distress/emotion vocabulary (Chinese-first, matching the product's primary
# audience). Substring matching is intentional — CJK has no word boundaries.
EMOTION_KEYWORDS: tuple[str, ...] = (
    "烦",
    "焦虑",
    "沮丧",
    "自责",
    "压力大",
    "心情不好",
    "难受",
    "害怕",
    "怕了",
    "崩溃",
    "想哭",
    "哭了",
    "受不了",
    "撑不住",
    "好累",
    "太累",
    "累死",
    "心累",
    "绝望",
    "无力",
    "委屈",
    "生气",
    "郁闷",
    "低落",
    "扛不住",
    "睡不着",
)

# English fallback for mixed-language input.
_EMOTION_KEYWORDS_EN: tuple[str, ...] = (
    "anxious",
    "anxiety",
    "frustrated",
    "depressed",
    "overwhelmed",
    "exhausted",
    "burned out",
    "burnt out",
    "scared",
    "hopeless",
    "can't take",
    "fed up",
)


def detect_affect(text: str | None) -> list[str]:
    """Return the matched emotion keywords (empty list == no affect signal).

    Pure and deterministic; never raises. Callers treat a non-empty result as
    "the user is likely carrying emotion right now — respond to the feeling
    before the data".
    """
    if not text:
        return []
    matched = [kw for kw in EMOTION_KEYWORDS if kw in text]
    lowered = text.casefold()
    matched.extend(kw for kw in _EMOTION_KEYWORDS_EN if kw in lowered)
    return matched


def is_affect_hit(text: str | None) -> bool:
    return bool(detect_affect(text))
