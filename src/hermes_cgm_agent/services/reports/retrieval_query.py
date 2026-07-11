from __future__ import annotations

from hermes_cgm_agent.domain import GlucoseAggregate, GlucoseEvent, GlucoseEventType


def build_authoritative_query(
    *,
    aggregate: GlucoseAggregate,
    detected_events: list[GlucoseEvent],
    population: str | None = None,
    report_type: str = "daily",
) -> tuple[str, str | None]:
    """Build a deterministic medical-KB query from report-window facts.

    This intentionally does not call an LLM. The report pipeline already has the
    relevant facts (aggregate metrics, data-quality coverage, detected events);
    retrieval should ask for the clinical guidance those facts imply, not for a
    generic "daily review" string.
    """
    terms: list[str] = []

    event_types = {str(event.event_type) for event in detected_events}
    alert_events = {str(event.event_type) for event in detected_events if str(event.severity) == "alert"}
    if GlucoseEventType.HYPO.value in event_types or GlucoseEventType.OVERNIGHT_LOW.value in event_types:
        terms.extend([
            "低血糖",
            "15 克碳水",
            "15 分钟",
            "hypoglycemia",
            "low glucose",
            "15 g carbohydrate",
            "15 minutes",
            "3.9",
            "70",
        ])
        if GlucoseEventType.HYPO.value in alert_events or GlucoseEventType.OVERNIGHT_LOW.value in alert_events:
            terms.extend(["严重低血糖", "胰高血糖素", "severe hypoglycemia", "glucagon", "3.0", "54"])
    if GlucoseEventType.HYPER.value in event_types:
        terms.extend(["高血糖", "酮体", "hyperglycemia", "ketone", "13.9", "250"])
        if GlucoseEventType.HYPER.value in alert_events:
            terms.extend(["DKA", "酮症酸中毒", "urgent care", "ketones"])
    if GlucoseEventType.RAPID_RISE.value in event_types or GlucoseEventType.RAPID_FALL.value in event_types:
        terms.extend(["血糖快速波动", "运动", "rapid glucose change", "exercise", "CGM trend arrow"])
    if GlucoseEventType.DATA_GAP.value in event_types or aggregate.data_coverage < 70:
        terms.extend(["CGM 数据质量", "佩戴时间", "CGM data quality", "wear time", "14 天", "14 days", "70%"])

    if aggregate.cv is not None and aggregate.cv > 36:
        terms.extend(["变异系数", "稳定性", "coefficient of variation", "CV", "glycemic variability", "36"])
    if aggregate.tbr is not None and aggregate.tbr > 4:
        terms.extend(["低于目标范围", "time below range", "TBR", "hypoglycemia", "4%", "4"])
    if aggregate.tar is not None and aggregate.tar > 25:
        terms.extend(["高于目标范围", "time above range", "TAR", "hyperglycemia", "180", "10.0", "25%", "25"])
    if aggregate.tir is not None and aggregate.tir < 70:
        terms.extend(["目标范围内时间", "time in range", "TIR", "target range", "70%", "70"])

    population_filter = _population_filter(population)
    if population_filter:
        terms.extend(_population_terms(population_filter))

    if not terms:
        terms.extend(["血糖管理", "目标范围", "glucose management", "time in range", "TIR", str(report_type)])

    return " ".join(_dedupe(terms)), population_filter


def _population_filter(population: str | None) -> str | None:
    if population is None:
        return None
    value = str(population).strip()
    return value or None


def _population_terms(population: str) -> list[str]:
    text = population.lower()
    if "preg" in text or "gdm" in text or "gestation" in text:
        return ["妊娠", "孕期", "pregnancy", "gestational diabetes", "TIR", "3.5", "7.8"]
    if "elder" in text or "older" in text or "geriat" in text:
        return ["老年", "高风险", "older adults", "elderly", "high risk", "TIR", "50%"]
    if "pedi" in text or "child" in text or "adolesc" in text:
        return ["儿童", "青少年", "pediatric", "children", "TIR", "HbA1c"]
    if "inpatient" in text or "hospital" in text or "icu" in text:
        return ["inpatient", "hospital", "hypoglycemia", "hyperglycemia"]
    return [population]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
