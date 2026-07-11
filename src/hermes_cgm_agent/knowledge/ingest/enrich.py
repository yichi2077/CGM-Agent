"""Deterministic claim-card enrichment (D059).

The ingestion pipeline is fully automated: Hermes extracts bilingual claim
cards, then this module enriches them WITHOUT any further LLM call so the step
is reproducible and unit-testable. Two concerns:

- ``enrich_synonyms`` — grow a card's ``synonyms`` so BM25 (CJK-bigram, sparse)
  can recall it from how a real user actually phrases things: cross-unit
  (mmol/L ⇄ mg/dL), bilingual term pairs, and colloquial Chinese.
- ``verify_bilingual_and_numbers`` — catch cards a translation step would make
  un-citable: an empty/placeholder side, or a Chinese/English number-set
  mismatch (a dropped threshold means the report narrative can quote a number
  the card no longer backs, and the strict citation gate would block it).

Everything here is pure and deterministic — no I/O, no model calls.
"""

from __future__ import annotations

import re
from typing import Any

from hermes_cgm_agent.domain.cgm import MGDL_PER_MMOLL

# ── bilingual term pairs ──────────────────────────────────────────────
# When either side of a pair appears in a card, BOTH tokens are added so a
# query in either language/register recalls the card. Kept explicit (not a
# translation model) so the mapping is auditable.
_TERM_PAIRS: tuple[tuple[str, str], ...] = (
    ("TIR", "目标范围内时间"),
    ("TBR", "低于目标范围时间"),
    ("TAR", "高于目标范围时间"),
    ("GMI", "血糖管理指标"),
    ("CV", "变异系数"),
    ("HbA1c", "糖化血红蛋白"),
    ("A1c", "糖化血红蛋白"),
    ("AGP", "动态血糖图谱"),
    ("glucagon", "胰高血糖素"),
    ("ketone", "酮体"),
    ("ketone", "酮"),
    ("ketoacidosis", "酮症酸中毒"),
    ("DKA", "糖尿病酮症酸中毒"),
    ("hypoglycemia", "低血糖"),
    ("hyperglycemia", "高血糖"),
    ("time in range", "目标范围内时间"),
    ("sick day", "病假日"),
    ("illness", "生病"),
    ("exercise", "运动"),
    ("physical activity", "体力活动"),
    ("alcohol", "饮酒"),
    ("postprandial", "餐后"),
    ("compression low", "压迫性低值"),
    ("data quality", "数据质量"),
    ("wear time", "佩戴时间"),
    ("interstitial fluid lag", "组织间液滞后"),
)

# ── colloquial phrase templates keyed by topic signal ─────────────────
# Each entry: (signal tokens that mark the topic, colloquial Chinese a real
# user would type). Signals are matched case-insensitively against the card's
# title+claim+tags. These are how patients ask — the BM25 index otherwise only
# holds clinical phrasing.
_COLLOQUIAL: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("hypoglycemia", "低血糖", "hypo"),
     ("血糖低了", "低血糖了怎么办", "手抖", "发慌", "出冷汗", "血糖太低")),
    (("exercise", "运动", "physical activity"),
     ("运动会不会低血糖", "锻炼血糖", "跑步血糖", "运动后血糖")),
    (("sick", "病假", "illness", "sick day"),
     ("生病了要不要停药", "感冒发烧血糖", "生病血糖高")),
    (("compression", "压迫", "artifact"),
     ("睡觉压到传感器", "半夜假性低血糖", "压着胳膊血糖低")),
    (("data quality", "wear time", "佩戴", "coverage"),
     ("数据不够准吗", "佩戴时间够不够", "CGM数据质量", "传感器数据不完整")),
    (("hyperglycemia", "高血糖", "ketone", "酮"),
     ("血糖太高", "要不要查酮体", "血糖高怎么办")),
    (("pregnan", "妊娠", "孕", "gestational"),
     ("孕期血糖", "怀孕血糖目标", "妊娠糖尿病")),
    (("driving", "驾驶", "开车"),
     ("开车前血糖", "开车会不会低血糖")),
    (("alcohol", "饮酒", "酒精"),
     ("喝酒血糖", "喝酒后低血糖")),
    (("postprandial", "餐后", "after meal"),
     ("饭后血糖", "餐后两小时", "吃完饭血糖目标")),
    (("nocturnal", "夜间", "overnight", "睡前"),
     ("夜间低血糖", "半夜血糖低", "睡前血糖")),
    (("pediatric", "儿童", "青少年", "child"),
     ("孩子血糖", "儿童血糖目标")),
    (("elderly", "老年", "older", "geriatr"),
     ("老年人血糖目标", "老人血糖")),
)

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
# A glucose value bound to a unit, optionally the low end of a "A–B unit" range.
_UNIT = r"(mg/d[lL]|mmol/[lL])"
_RANGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[–\-~]\s*(\d+(?:\.\d+)?)\s*" + _UNIT)
_SINGLE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*" + _UNIT)

_PLACEHOLDER_PREFIXES = ("待人工翻译", "待核验", "待翻译", "todo", "tbd")


def _to_mmol(mgdl: float) -> str:
    return f"{round(mgdl / MGDL_PER_MMOLL, 1)}"


def _to_mgdl(mmol: float) -> str:
    return f"{round(mmol * MGDL_PER_MMOLL)}"


def _dual_unit_tokens(text: str) -> list[str]:
    """Cross-unit recall tokens for every value bound to a glucose unit.

    A card written in mg/dL still needs to be found by a user who reads mmol/L
    (the AiDEX ecosystem default) and vice versa, so each bound value emits its
    counterpart-unit spelling as a synonym.
    """
    tokens: list[str] = []

    def _emit(value_str: str, unit: str) -> None:
        value = float(value_str)
        if unit.lower().startswith("mg"):
            tokens.append(f"{_to_mmol(value)} mmol/L")
        else:
            tokens.append(f"{_to_mgdl(value)} mg/dL")

    consumed: list[tuple[int, int]] = []
    for m in _RANGE_RE.finditer(text):
        _emit(m.group(1), m.group(3))
        _emit(m.group(2), m.group(3))
        consumed.append(m.span())
    for m in _SINGLE_RE.finditer(text):
        # skip the low end of a range already handled above
        if any(start <= m.start() < end for start, end in consumed):
            continue
        _emit(m.group(1), m.group(2))
    return tokens


def enrich_synonyms(card: dict[str, Any]) -> list[str]:
    """Return the card's synonyms grown with deterministic recall aids.

    Existing synonyms are preserved and lead the list; generated tokens are
    appended and the whole list is de-duplicated case-insensitively while
    keeping first-seen order. Pure — does not mutate ``card``.
    """
    haystack = " ".join(
        str(card.get(k) or "")
        for k in ("title", "claim_zh", "claim_en")
    )
    tags_blob = " ".join(str(t) for t in (card.get("tags") or []))
    signal = f"{haystack} {tags_blob}".lower()

    generated: list[str] = []
    # (a) cross-unit spellings from both language claims
    generated.extend(_dual_unit_tokens(f"{card.get('claim_zh') or ''} {card.get('claim_en') or ''}"))
    # (b) bilingual term pairs
    for en, zh in _TERM_PAIRS:
        if en.lower() in signal or zh in signal:
            generated.append(en)
            generated.append(zh)
    # (c) colloquial phrasing by topic
    for signals, phrases in _COLLOQUIAL:
        if any(s.lower() in signal for s in signals):
            generated.extend(phrases)

    ordered = list(card.get("synonyms") or []) + generated
    seen: set[str] = set()
    result: list[str] = []
    for token in ordered:
        token = str(token).strip()
        if not token:
            continue
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(token)
    return result


def _significant_numbers(text: str) -> set[str]:
    numbers = set(_NUMBER_RE.findall(text or ""))
    for value, unit in _SINGLE_RE.findall(text or ""):
        if unit.lower().startswith("mg"):
            numbers.add(_to_mmol(float(value)))
        else:
            numbers.add(_to_mgdl(float(value)))
    return numbers


def verify_bilingual_and_numbers(card: dict[str, Any]) -> list[str]:
    """Return a list of problems (empty == clean).

    Guards the two failure modes an automated translate/extract step creates:
    a missing or placeholder language side, and a number that exists in one
    language but not the other (a dropped threshold the report could quote yet
    the card would no longer back, tripping the strict citation gate).
    """
    problems: list[str] = []
    zh = str(card.get("claim_zh") or "").strip()
    en = str(card.get("claim_en") or "").strip()
    card_id = str(card.get("card_id") or "<no-id>")

    if not zh:
        problems.append(f"{card_id}: claim_zh is empty")
    if not en:
        problems.append(f"{card_id}: claim_en is empty")
    for label, value in (("claim_zh", zh), ("claim_en", en)):
        low = value.lower()
        if any(low.startswith(p) for p in _PLACEHOLDER_PREFIXES):
            problems.append(f"{card_id}: {label} is an untranslated placeholder")

    if zh and en:
        zh_nums = _significant_numbers(zh)
        en_nums = _significant_numbers(en)
        only_zh = sorted(zh_nums - en_nums)
        only_en = sorted(en_nums - zh_nums)
        if only_zh:
            problems.append(f"{card_id}: numbers only in claim_zh: {only_zh}")
        if only_en:
            problems.append(f"{card_id}: numbers only in claim_en: {only_en}")
    return problems


def enrich_card(card: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``card`` with enriched synonyms. Verification is a
    separate reporting concern the caller runs before merge."""
    enriched = dict(card)
    enriched["synonyms"] = enrich_synonyms(card)
    return enriched
