"""Generate a deterministic, realistic-density 14-day CGM CSV fixture.

Produces ~4,032 points at a 5-minute cadence (the real CGM density referenced by
the technical architecture report) so that LBGI/HBGI, near-high/far-low
compression, and event detection can be validated against lifelike data instead
of the sparse 28-point smoke fixture.

The signal is fully deterministic (seeded), so the committed CSV is reproducible:

    python examples/g0_g7_demo/generate_realistic_cgm.py

Physiology baked in (so detectors have something to find):
- Circadian baseline with a dawn rise.
- Three postprandial spikes per day (breakfast/lunch/dinner).
- A couple of overnight lows on specific days.
- One multi-hour sensor gap (rows omitted) to exercise data-gap detection.
"""

from __future__ import annotations

import csv
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

START = datetime(2026, 5, 18, 0, 0, tzinfo=timezone.utc)
DAYS = 14
INTERVAL_MIN = 5
SEED = 20260518
OUTPUT = Path(__file__).with_name("cgm_14d_realistic.csv")

# Local meal times (UTC hours here for simplicity of the fixture).
MEAL_HOURS = (7, 12, 19)
# Days (0-indexed) that get an early-morning low around 03:00-04:00 UTC.
OVERNIGHT_LOW_DAYS = (3, 9)
# A sensor gap: skip emission for this UTC datetime range on day 6.
GAP_START = START + timedelta(days=6, hours=14)
GAP_END = START + timedelta(days=6, hours=17, minutes=30)


def baseline(ts: datetime) -> float:
    hour = ts.hour + ts.minute / 60
    # Smooth circadian baseline ~105 mg/dL with a dawn rise.
    return 105 + 10 * math.sin((hour - 3) / 24 * 2 * math.pi)


def meal_effect(ts: datetime) -> float:
    effect = 0.0
    hour = ts.hour + ts.minute / 60
    for meal_hour in MEAL_HOURS:
        # Postprandial bump peaking ~75 min after the meal, decaying over ~3h.
        delta = hour - meal_hour
        if 0 <= delta <= 3:
            effect += 70 * math.exp(-((delta - 1.25) ** 2) / 0.6)
    return effect


def overnight_low(ts: datetime, day_index: int) -> float:
    if day_index not in OVERNIGHT_LOW_DAYS:
        return 0.0
    hour = ts.hour + ts.minute / 60
    if 2.5 <= hour <= 4.5:
        return -55 * math.exp(-((hour - 3.5) ** 2) / 0.3)
    return 0.0


def main() -> None:
    rng = random.Random(SEED)
    rows = []
    total_steps = DAYS * 24 * 60 // INTERVAL_MIN
    for step in range(total_steps):
        ts = START + timedelta(minutes=step * INTERVAL_MIN)
        if GAP_START <= ts < GAP_END:
            continue  # sensor gap: emit no point
        day_index = (ts - START).days
        value = (
            baseline(ts)
            + meal_effect(ts)
            + overnight_low(ts, day_index)
            + rng.uniform(-6, 6)
        )
        value = max(40, min(360, value))
        rows.append(
            {
                "timestamp": ts.isoformat(),
                "value": round(value, 1),
                "unit": "mg/dL",
                "device_id": "demo-sensor",
                "record_id": f"rec-{step:05d}",
            }
        )

    with OUTPUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["timestamp", "value", "unit", "device_id", "record_id"]
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} points to {OUTPUT.name}")


if __name__ == "__main__":
    main()
