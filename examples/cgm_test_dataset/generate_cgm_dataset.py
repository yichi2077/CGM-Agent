"""Generate a deterministic multi-sensor CGM test dataset (default 3 x 14 days).

Why this exists
---------------
Public CGM datasets (ShanghaiT1DM, Hall 2018, the Awesome-CGM collection) top out
at roughly a single ~14-day sensor wear per subject, so "30 days (3x14)" of data
for one person inherently means composing several sensor sessions. Rather than
stitch real traces of mismatched lengths/quality, this script emits a controlled,
fully reproducible signal that exercises every part of the pipeline:

  import-cgm -> normalize (gap + warmup detection) -> analytics (TIR / LBGI / HBGI
  / events) -> reports.generate -> memory candidates.

Structure (default)
-------------------
- 3 sensor sessions, 14 days each, 5-minute cadence => ~12,096 points (3x14 = 42
  days total). The user brief said "30 days (3x14)"; 3x14 is 42, so the default
  follows the explicit 3x14 structure. Pass --days-per-sensor 10 for exactly 30.
- Each session uses a distinct device_id (a separate physical sensor) and starts
  with a ~2h warmup gap (no points) to mark the sensor change and give the
  normalizer a real gap/boundary to find.
- A cross-sensor story so trend/report output is meaningful:
    Sensor A  decent control      (mean ~135, TIR ~75%, a couple nocturnal lows)
    Sensor B  bad/stress week     (higher baseline, larger spikes, TIR ~50%)
    Sensor C  recovery            (tighter control, smaller spikes, TIR ~85%)
- One mid-session signal dropout (~3h) inside sensor B to exercise gap detection.

Timestamps are emitted as NAIVE LOCAL time (no offset). Import with the matching
zone (CLI default is Asia/Shanghai), which is realistic for a device CSV export:

    python examples/cgm_test_dataset/generate_cgm_dataset.py
    PYTHONPATH=src python -m hermes_cgm_agent import-cgm \\
        --file examples/cgm_test_dataset/cgm_3x14.csv --format csv \\
        --user-id demo-user --timezone Asia/Shanghai

The output CSV and manifest.json are deterministic for a given --seed, so they can
be committed and used as a stable test fixture.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


@dataclass(frozen=True)
class SensorProfile:
    """Per-sensor physiology knobs that shape one 14-day session."""

    device_id: str
    label: str
    baseline_mg_dl: float          # mean circadian baseline
    meal_amplitude: float          # postprandial peak height (mg/dL above baseline)
    noise_sd: float                # minute-to-minute sensor noise
    nocturnal_low_days: tuple[int, ...]  # day offsets (within session) with a ~03:30 low
    daytime_high_bias: float       # extra drift added 10:00-22:00 (a "stuck high" week)


# Local meal times (hour of day) -> postprandial bumps.
MEAL_HOURS = (7.5, 12.5, 18.75)
WARMUP_HOURS = 2.0          # leading no-data gap at each sensor change
INTERVAL_DEFAULT = 5        # minutes between points
UNIT = "mg/dL"

DEFAULT_PROFILES = (
    SensorProfile(
        device_id="SENSOR-A-7F3C",
        label="baseline / decent control",
        baseline_mg_dl=108.0,
        meal_amplitude=68.0,
        noise_sd=6.0,
        nocturnal_low_days=(3, 9),
        daytime_high_bias=0.0,
    ),
    SensorProfile(
        device_id="SENSOR-B-2A91",
        label="stress / holiday week (worse control)",
        baseline_mg_dl=128.0,
        meal_amplitude=104.0,
        noise_sd=9.0,
        nocturnal_low_days=(),
        daytime_high_bias=22.0,
    ),
    SensorProfile(
        device_id="SENSOR-C-5E08",
        label="recovery (tighter control)",
        baseline_mg_dl=104.0,
        meal_amplitude=46.0,
        noise_sd=5.0,
        nocturnal_low_days=(6,),
        daytime_high_bias=0.0,
    ),
)


def circadian(local_dt: datetime, baseline: float) -> float:
    hour = local_dt.hour + local_dt.minute / 60.0
    # Smooth baseline with a dawn rise peaking ~07:00.
    return baseline + 10.0 * math.sin((hour - 3.0) / 24.0 * 2.0 * math.pi)


def meal_effect(local_dt: datetime, amplitude: float) -> float:
    hour = local_dt.hour + local_dt.minute / 60.0
    effect = 0.0
    for meal_hour in MEAL_HOURS:
        delta = hour - meal_hour
        if 0.0 <= delta <= 3.0:
            # Peak ~75 min post-meal, decaying over ~3h.
            effect += amplitude * math.exp(-((delta - 1.25) ** 2) / 0.6)
    return effect


def nocturnal_low(local_dt: datetime, day_in_session: int, low_days: tuple[int, ...]) -> float:
    if day_in_session not in low_days:
        return 0.0
    hour = local_dt.hour + local_dt.minute / 60.0
    if 2.5 <= hour <= 4.5:
        return -58.0 * math.exp(-((hour - 3.5) ** 2) / 0.3)
    return 0.0


def daytime_bias(local_dt: datetime, bias: float) -> float:
    if bias == 0.0:
        return 0.0
    hour = local_dt.hour + local_dt.minute / 60.0
    return bias if 10.0 <= hour <= 22.0 else 0.0


def _session_dropout(day_in_session: int, local_dt: datetime, profile: SensorProfile) -> bool:
    """A single ~3h signal loss inside the 'bad' sensor to test gap detection."""
    if "stress" not in profile.label:
        return False
    if day_in_session != 5:
        return False
    hour = local_dt.hour + local_dt.minute / 60.0
    return 14.0 <= hour < 17.0


def generate(
    *,
    profiles: tuple[SensorProfile, ...],
    start: datetime,
    days_per_sensor: int,
    interval_min: int,
    seed: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    rng = random.Random(seed)
    rows: list[dict[str, object]] = []
    sessions: list[dict[str, object]] = []
    steps_per_session = days_per_sensor * 24 * 60 // interval_min
    record_index = 0
    cursor = start

    for sensor_index, profile in enumerate(profiles):
        session_start = cursor
        warmup_until = session_start + timedelta(hours=WARMUP_HOURS)
        first_ts: datetime | None = None
        last_ts: datetime | None = None
        emitted = 0

        for step in range(steps_per_session):
            ts = session_start + timedelta(minutes=step * interval_min)
            # Warmup: every sensor change drops its first WARMUP_HOURS of data.
            if ts < warmup_until:
                continue
            day_in_session = (ts - session_start).days
            if _session_dropout(day_in_session, ts, profile):
                continue

            value = (
                circadian(ts, profile.baseline_mg_dl)
                + meal_effect(ts, profile.meal_amplitude)
                + nocturnal_low(ts, day_in_session, profile.nocturnal_low_days)
                + daytime_bias(ts, profile.daytime_high_bias)
                + rng.uniform(-profile.noise_sd, profile.noise_sd)
            )
            value = max(40.0, min(360.0, value))
            rows.append(
                {
                    "timestamp": ts.isoformat(timespec="seconds"),
                    "value": round(value, 1),
                    "unit": UNIT,
                    "device_id": profile.device_id,
                    "record_id": f"{profile.device_id}-{record_index:06d}",
                }
            )
            record_index += 1
            emitted += 1
            first_ts = first_ts or ts
            last_ts = ts

        sessions.append(
            {
                "sensor_index": sensor_index + 1,
                "device_id": profile.device_id,
                "label": profile.label,
                "session_start_local": session_start.isoformat(timespec="seconds"),
                "first_point_local": first_ts.isoformat(timespec="seconds") if first_ts else None,
                "last_point_local": last_ts.isoformat(timespec="seconds") if last_ts else None,
                "warmup_gap_hours": WARMUP_HOURS,
                "point_count": emitted,
            }
        )
        # Next sensor begins immediately after this one's nominal 14-day wear.
        cursor = session_start + timedelta(days=days_per_sensor)

    return rows, sessions


def main() -> None:
    here = Path(__file__).parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days-per-sensor", type=int, default=14)
    parser.add_argument("--interval-min", type=int, default=INTERVAL_DEFAULT)
    parser.add_argument("--seed", type=int, default=20260606)
    parser.add_argument(
        "--start",
        default="2026-04-25T00:00:00",
        help="Naive local start datetime (ISO, no offset).",
    )
    parser.add_argument("--out", default=str(here / "cgm_3x14.csv"))
    parser.add_argument("--manifest", default=str(here / "manifest.json"))
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start)
    rows, sessions = generate(
        profiles=DEFAULT_PROFILES,
        start=start,
        days_per_sensor=args.days_per_sensor,
        interval_min=args.interval_min,
        seed=args.seed,
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["timestamp", "value", "unit", "device_id", "record_id"]
        )
        writer.writeheader()
        writer.writerows(rows)

    total_days = len(DEFAULT_PROFILES) * args.days_per_sensor
    manifest = {
        "timezone": "Asia/Shanghai",
        "unit": UNIT,
        "interval_minutes": args.interval_min,
        "sensor_count": len(DEFAULT_PROFILES),
        "days_per_sensor": args.days_per_sensor,
        "total_days": total_days,
        "total_points": len(rows),
        "csv": out_path.name,
        "sessions": sessions,
        "note": (
            "Naive local timestamps; import with --timezone Asia/Shanghai. "
            "3 x 14 = 42 days by default; use --days-per-sensor 10 for exactly 30."
        ),
    }
    Path(args.manifest).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"Wrote {len(rows)} points across {len(sessions)} sensor sessions "
          f"({total_days} days) to {out_path}")
    for session in sessions:
        print(
            f"  sensor {session['sensor_index']} [{session['device_id']}] "
            f"{session['first_point_local']} -> {session['last_point_local']} "
            f"({session['point_count']} pts) — {session['label']}"
        )


if __name__ == "__main__":
    main()
