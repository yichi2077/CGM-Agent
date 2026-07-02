"""Generate the default synthetic CGM dataset for local engineering E2E tests.

The default fixture is one prediabetes-style user over 14 days, sampled at native
1-minute resolution. It is meant to prove the software pipeline can run end to
end without real patients or a real CGM device. It is not clinical evidence and
must not be used to validate medical algorithm efficacy.
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
from statistics import mean, pstdev


TIMEZONE = "Asia/Shanghai"
UNIT = "mg/dL"
USER_ID = "demo-prediabetes-user"
DEVICE_ID = "VIRTUAL-AIDEX-X-001"
INTERVAL_DEFAULT = 1
DAYS_DEFAULT = 14
WARMUP_MINUTES = 120


@dataclass(frozen=True)
class BehaviorEvent:
    event_id: str
    type: str
    ts_start: datetime
    ts_end: datetime | None
    subtype: str | None
    value: float | None
    unit: str | None
    note: str

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "type": self.type,
            "ts_start": self.ts_start.isoformat(timespec="seconds"),
            "ts_end": self.ts_end.isoformat(timespec="seconds") if self.ts_end else None,
            "subtype": self.subtype,
            "value": self.value,
            "unit": self.unit,
            "note": self.note,
        }


def build_behavior_events(start: datetime, days: int) -> list[BehaviorEvent]:
    events: list[BehaviorEvent] = []
    stress_days = {4, 5, 10}
    poor_sleep_days = {3, 9}
    walk_days = {0, 1, 2, 4, 6, 8, 10, 12}

    def emit(
        *,
        day: int,
        hour: int,
        minute: int,
        event_type: str,
        subtype: str | None,
        value: float | None,
        unit: str | None,
        duration_min: int = 0,
        note: str,
    ) -> None:
        ts = start + timedelta(days=day, hours=hour, minutes=minute)
        ts_end = ts + timedelta(minutes=duration_min) if duration_min else None
        events.append(
            BehaviorEvent(
                event_id=f"evt-{event_type}-{day:02d}-{len(events):03d}",
                type=event_type,
                ts_start=ts,
                ts_end=ts_end,
                subtype=subtype,
                value=value,
                unit=unit,
                note=note,
            )
        )

    for day in range(days):
        weekend = (start + timedelta(days=day)).weekday() >= 5
        stress = day in stress_days
        poor_sleep = day in poor_sleep_days
        breakfast_carbs = 42 + (10 if poor_sleep else 0)
        lunch_carbs = 58 + (12 if stress else 0)
        dinner_carbs = 52 + (12 if weekend else 0)
        emit(day=day, hour=7, minute=35, event_type="meal", subtype="breakfast",
             value=breakfast_carbs, unit="grams", note="breakfast carbs")
        emit(day=day, hour=12, minute=35, event_type="meal", subtype="lunch",
             value=lunch_carbs, unit="grams", note="lunch carbs")
        emit(day=day, hour=18, minute=50, event_type="meal", subtype="dinner",
             value=dinner_carbs, unit="grams", note="dinner carbs")
        if day in walk_days:
            emit(day=day, hour=19, minute=50, event_type="exercise", subtype="post_meal_walk",
                 value=25, unit="minutes", duration_min=25, note="post-dinner walk")
        if stress:
            emit(day=day, hour=14, minute=30, event_type="symptom", subtype="stress",
                 value=None, unit=None, duration_min=180, note="stressful afternoon")
        if poor_sleep:
            emit(day=day, hour=0, minute=0, event_type="note", subtype="poor_sleep",
                 value=5.5, unit="hours", duration_min=360, note="short sleep")

    return sorted(events, key=lambda item: item.ts_start)


def generate(
    *,
    start: datetime,
    days: int,
    interval_min: int,
    seed: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    if interval_min < 1:
        raise ValueError("interval_min must be positive")
    rng = random.Random(seed)
    events = build_behavior_events(start, days)
    rows: list[dict[str, object]] = []
    artifacts: list[dict[str, object]] = []
    steps = days * 24 * 60 // interval_min
    warmup_until = start + timedelta(minutes=WARMUP_MINUTES)
    record_index = 0
    sensor_value = 108.0
    ar_noise = 0.0
    drift = 0.0
    previous_emitted_value: float | None = None

    for step in range(steps):
        ts = start + timedelta(minutes=step * interval_min)
        if ts < warmup_until:
            continue
        if _is_dropout(ts, start):
            if _artifact_starts(ts, start, "dropout"):
                artifacts.append(_artifact("dropout", ts, 30, "Short missing-data window."))
            continue

        target = _physiology_target(ts, start, events)
        drift = max(-4.0, min(4.0, drift + rng.uniform(-0.025, 0.025)))
        ar_noise = 0.93 * ar_noise + rng.gauss(0.0, 0.42)
        artifact_effect, status = _artifact_effect(ts, start, rng)
        observed_target = target + drift + ar_noise + artifact_effect
        sensor_value = sensor_value + 0.14 * (observed_target - sensor_value)
        if previous_emitted_value is not None:
            sensor_value = _clamp(sensor_value, previous_emitted_value - 3.8, previous_emitted_value + 3.8)
        sensor_value = _clamp(sensor_value, 45.0, 260.0)
        trend = _trend(sensor_value, previous_emitted_value)
        event_ids = _active_event_ids(events, ts)

        if status and _artifact_starts(ts, start, status):
            artifacts.append(_artifact(status, ts, 20 if status == "compression_low" else 15, status))

        rows.append(
            {
                "timestamp": ts.isoformat(timespec="seconds"),
                "value": round(sensor_value, 1),
                "unit": UNIT,
                "device_id": DEVICE_ID,
                "record_id": f"{DEVICE_ID}-{record_index:06d}",
                "trend": trend,
                "status": status,
                "artifact": status,
                "event_ids": ";".join(event_ids),
            }
        )
        previous_emitted_value = sensor_value
        record_index += 1

    return rows, [event.to_dict() for event in events], artifacts


def _physiology_target(ts: datetime, start: datetime, events: list[BehaviorEvent]) -> float:
    hour = ts.hour + ts.minute / 60.0
    day = (ts - start).days
    baseline = 106.0
    baseline += 7.5 * math.exp(-((hour - 6.2) ** 2) / 5.0)
    baseline -= 4.0 * math.exp(-((hour - 2.8) ** 2) / 7.0)
    baseline += 1.7 * math.sin((day / 13.0) * math.pi)
    value = baseline

    for event in events:
        delta_min = (ts - event.ts_start).total_seconds() / 60.0
        if event.type == "meal" and event.value is not None and 0 <= delta_min <= 240:
            peak = 72.0 if event.subtype != "dinner" else 86.0
            amplitude = 18.0 + event.value * 0.82
            value += amplitude * _gamma_bump(delta_min, peak)
        elif event.type == "exercise" and 0 <= delta_min <= 180:
            value -= 16.0 * _gamma_bump(delta_min, 55.0)
        elif event.type == "symptom" and event.subtype == "stress" and -30 <= delta_min <= 330:
            value += 12.0 * _smooth_window(delta_min, 0.0, 300.0)
        elif event.type == "note" and event.subtype == "poor_sleep" and 0 <= delta_min <= 720:
            value += 8.0 * _smooth_window(delta_min, 0.0, 660.0)

    return value


def _gamma_bump(delta_min: float, peak_min: float) -> float:
    if delta_min < 0:
        return 0.0
    x = max(0.0, delta_min / peak_min)
    return x * math.exp(1.0 - x)


def _smooth_window(delta_min: float, start_min: float, end_min: float) -> float:
    if delta_min < start_min or delta_min > end_min:
        return 0.0
    ramp = min(1.0, max(0.0, (delta_min - start_min) / 45.0))
    fade = min(1.0, max(0.0, (end_min - delta_min) / 45.0))
    return min(ramp, fade)


def _artifact_effect(ts: datetime, start: datetime, rng: random.Random) -> tuple[float, str]:
    day = (ts - start).days
    hour = ts.hour + ts.minute / 60.0
    if day == 6 and 3.1 <= hour <= 3.55:
        center = 3.32
        return -58.0 * math.exp(-((hour - center) ** 2) / 0.025), "compression_low"
    if day == 11 and 16.0 <= hour <= 16.25:
        return rng.uniform(-12.0, 12.0), "sensor_noise"
    return 0.0, ""


def _is_dropout(ts: datetime, start: datetime) -> bool:
    day = (ts - start).days
    hour = ts.hour + ts.minute / 60.0
    return day == 8 and 14.33 <= hour < 14.83


def _artifact_starts(ts: datetime, start: datetime, status: str) -> bool:
    minute_of_day = ts.hour * 60 + ts.minute
    if status == "dropout":
        return (ts - start).days == 8 and minute_of_day == 14 * 60 + 20
    if status == "compression_low":
        return (ts - start).days == 6 and minute_of_day == 3 * 60 + 6
    if status == "sensor_noise":
        return (ts - start).days == 11 and minute_of_day == 16 * 60
    return False


def _active_event_ids(events: list[BehaviorEvent], ts: datetime) -> list[str]:
    active: list[str] = []
    for event in events:
        end = event.ts_end or event.ts_start + timedelta(hours=4 if event.type == "meal" else 1)
        if event.ts_start <= ts <= end:
            active.append(event.event_id)
    return active


def _trend(value: float, previous: float | None) -> str:
    if previous is None:
        return "flat"
    delta = value - previous
    if delta >= 2.5:
        return "rising_fast"
    if delta >= 0.8:
        return "rising"
    if delta <= -2.5:
        return "falling_fast"
    if delta <= -0.8:
        return "falling"
    return "stable"


def _artifact(status: str, ts: datetime, duration_minutes: int, note: str) -> dict[str, object]:
    return {
        "type": status,
        "ts_start": ts.isoformat(timespec="seconds"),
        "ts_end": (ts + timedelta(minutes=duration_minutes)).isoformat(timespec="seconds"),
        "note": note,
    }


def _metrics(rows: list[dict[str, object]]) -> dict[str, object]:
    values = [float(row["value"]) for row in rows]
    tir = sum(70 <= value <= 180 for value in values) / len(values) * 100
    tar = sum(value > 180 for value in values) / len(values) * 100
    tbr = sum(value < 70 for value in values) / len(values) * 100
    avg = mean(values)
    return {
        "min": round(min(values), 1),
        "max": round(max(values), 1),
        "mean": round(avg, 1),
        "cv": round(pstdev(values) / avg * 100, 1),
        "tir": round(tir, 1),
        "tar": round(tar, 1),
        "tbr": round(tbr, 2),
    }


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def main() -> None:
    here = Path(__file__).parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=DAYS_DEFAULT)
    parser.add_argument("--interval-min", type=int, default=INTERVAL_DEFAULT)
    parser.add_argument("--seed", type=int, default=20260701)
    parser.add_argument(
        "--start",
        default="2026-04-25T00:00:00",
        help="Naive local start datetime (ISO, no offset).",
    )
    parser.add_argument("--out", default=str(here / "cgm_14d_1min.csv"))
    parser.add_argument("--manifest", default=str(here / "manifest.json"))
    parser.add_argument("--events-out", default=str(here / "behavior_events_14d.json"))
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start)
    rows, events, artifacts = generate(
        start=start,
        days=args.days,
        interval_min=args.interval_min,
        seed=args.seed,
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "timestamp",
        "value",
        "unit",
        "device_id",
        "record_id",
        "trend",
        "status",
        "artifact",
        "event_ids",
    ]
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    events_path = Path(args.events_out)
    events_path.write_text(json.dumps({"events": events}, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest = {
        "purpose": "engineering_e2e_fixture_not_clinical_validation",
        "user_id": USER_ID,
        "timezone": TIMEZONE,
        "unit": UNIT,
        "interval_minutes": args.interval_min,
        "default_emit_interval_minutes": 5,
        "days": args.days,
        "sensor_count": 1,
        "device_id": DEVICE_ID,
        "warmup_gap_minutes": WARMUP_MINUTES,
        "total_points": len(rows),
        "csv": out_path.name,
        "events_json": events_path.name,
        "artifacts": artifacts,
        "metrics": _metrics(rows),
        "note": (
            "Naive local timestamps; import with --timezone Asia/Shanghai. "
            "Values are synthetic prediabetes-style CGM points for local software testing."
        ),
    }
    Path(args.manifest).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"Wrote {len(rows)} points for {args.days} days to {out_path}")
    print(f"Wrote {len(events)} behavior events to {events_path}")
    print(json.dumps(manifest["metrics"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
