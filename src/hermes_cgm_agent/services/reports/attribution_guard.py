"""Attribution-consistency check for the LLM medical narrative (Issue #8).

The citation gate (`builder._apply_citation_gate`) verifies that every clinical
NUMBER in the narrative is backed by a retrieved authoritative card, and
`check_companion_text` verifies TONE — but neither verifies that a causal
ATTRIBUTION claim matches the deterministic metrics. The 14-day simulation
audit caught the gap: a 4.6 mg/dL steady-state fluctuation was narrated as a
"餐后小高峰" (postprandial spike) and sailed through both guards.

This module cross-checks attribution phrases against the window's aggregate.
Detection is deliberately conservative (few rules, hard thresholds) so a false
"inconsistent" flag never suppresses a genuine explanation: a flag only appends
a correction note — it never rewrites or blocks the narrative (the narrative
text is externally generated and must stay verbatim for the citation gate).
"""

from __future__ import annotations

import re
from typing import Any

from hermes_cgm_agent.domain import GlucoseAggregate

# A postprandial/spike claim requires actual above-range burden or meaningful
# variability. CV below this is a clinically flat trace (consensus "stable"
# threshold is 36%; 10% is far below any spike-compatible variability).
_SPIKE_MIN_CV = 10.0

# Attribution patterns → predicate that must hold for the claim to be
# metric-consistent. Each entry: (tag, compiled pattern, checker).
_HYPO_PATTERN = re.compile(r"低血糖|夜间低|偏低|hypoglyc", re.IGNORECASE)
_SPIKE_PATTERN = re.compile(r"餐后[小大]?高峰|餐后(血糖)?(升高|飙升)|postprandial", re.IGNORECASE)
_HYPER_PATTERN = re.compile(r"高血糖|血糖偏高|hyperglyc", re.IGNORECASE)
_VOLATILE_PATTERN = re.compile(r"波动(较|很|明显)?大|血糖不稳定|大幅波动", re.IGNORECASE)


def attribution_consistency_check(
    aggregate: GlucoseAggregate | dict[str, Any] | None,
    narrative: str,
) -> list[str]:
    """Return inconsistency tags (empty == consistent). Pure; never raises.

    Tags: ``attribution:<claim>~<metric-evidence>`` — e.g. a narrative claiming
    a postprandial spike while TAR is 0% and CV < 10% is flagged
    ``attribution:postprandial_spike~tar=0,cv<10``.
    """
    if not narrative or aggregate is None:
        return []
    if isinstance(aggregate, GlucoseAggregate):
        tar = aggregate.tar
        tbr = aggregate.tbr
        cv = aggregate.cv
    else:
        tar = aggregate.get("tar", aggregate.get("TAR"))
        tbr = aggregate.get("tbr", aggregate.get("TBR"))
        cv = aggregate.get("cv", aggregate.get("CV"))

    violations: list[str] = []
    # Postprandial-spike claim vs a flat, in-range trace (the Issue #8 case).
    if _SPIKE_PATTERN.search(narrative):
        no_tar = tar is not None and tar == 0
        flat_cv = cv is not None and cv < _SPIKE_MIN_CV
        if no_tar and flat_cv:
            violations.append("attribution:postprandial_spike~tar=0,cv<10")
    # Hyperglycemia claim vs zero above-range time.
    if _HYPER_PATTERN.search(narrative) and tar is not None and tar == 0:
        violations.append("attribution:hyperglycemia~tar=0")
    # Hypoglycemia claim vs zero below-range time.
    if _HYPO_PATTERN.search(narrative) and tbr is not None and tbr == 0:
        violations.append("attribution:hypoglycemia~tbr=0")
    # Volatility claim vs a flat trace.
    if _VOLATILE_PATTERN.search(narrative) and cv is not None and cv < _SPIKE_MIN_CV:
        violations.append("attribution:volatility~cv<10")
    return violations


# Correction note appended (never rewritten in place — verbatim narrative is a
# citation-gate invariant) when the narrative's attribution contradicts the
# deterministic metrics. Companion-tone compliant: soft, non-assertive.
ATTRIBUTION_CORRECTION_NOTE = (
    "注：上述归因与数据分析结果不完全一致，请以指标数据为准。"
)
