"""Device sampling-cadence inference (D053).

A 1-minute AiDEX-style feed measured against the historical 5-minute default
mis-tunes every cadence-derived threshold: data-gap detection waits 20 minutes
instead of 4, and minimum-episode gating over-pads by 4 minutes. Rather than
adding a cadence setting the user must know to configure, consumers infer the
cadence from the points they have ALREADY loaded — the median inter-reading
gap, robust against occasional real gaps — and fall back to the 5-minute
default when there is not enough data to tell.
"""

from __future__ import annotations

from datetime import datetime

DEFAULT_INTERVAL_MINUTES = 5
MIN_INTERVAL_MINUTES = 1
MAX_INTERVAL_MINUTES = 15
_MIN_SAMPLE_DELTAS = 5


def median_interval_minutes(
    timestamps: list[datetime],
    *,
    default: int = DEFAULT_INTERVAL_MINUTES,
) -> int:
    """Median gap between consecutive readings in whole minutes.

    Requires a handful of deltas before trusting the data (a 3-point window
    says nothing about cadence); clamps to [1, 15] so a corrupt series can
    never produce a zero or absurd interval.
    """
    ordered = sorted(timestamps)
    deltas = sorted(
        (later - earlier).total_seconds()
        for earlier, later in zip(ordered, ordered[1:])
        if later > earlier
    )
    if len(deltas) < _MIN_SAMPLE_DELTAS:
        return default
    median_seconds = deltas[len(deltas) // 2]
    minutes = round(median_seconds / 60)
    return max(MIN_INTERVAL_MINUTES, min(MAX_INTERVAL_MINUTES, minutes))
