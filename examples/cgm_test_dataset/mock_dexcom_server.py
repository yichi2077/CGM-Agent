"""A local mock of the Dexcom API v3, backed by the generated CGM CSV.

It lets the *real* DexcomClient / DexcomSyncService / `dexcom-sync` CLI run fully
offline against our 3x14-day dataset, so the whole ingest path (OAuth -> token
store -> dataRange -> chunked, time-ordered EGV pulls -> mapper -> dedup) can be
exercised without the (region-blocked) Dexcom sandbox.

Endpoints implemented (subset of Dexcom v3):
  POST /v3/oauth2/token           -> issues a fake bearer + refresh token
  GET  /v3/users/self/dataRange   -> min/max systemTime of the dataset
  GET  /v3/users/self/egvs        -> {"records": [...]} within [startDate,endDate)
  GET  /v3/users/self/events      -> {"records": []}  (CGM-only dataset)

Periodic / chronological simulation
-----------------------------------
With DEXCOM_MOCK_STREAM=1 (default), each call to /dataRange advances a visible
"end" cursor by DEXCOM_MOCK_STEP_DAYS (default 7). Because DexcomSyncService asks
for dataRange once per sync, calling `dexcom-sync --days 7` repeatedly pulls
[day0..7), [day7..14), ... in order — exactly like a scheduled poller seeing data
arrive over time. Re-running a window is idempotent (the repository dedups).
Hit /dataRange?reset=1 (or restart) to rewind the cursor.

Run:
    python examples/cgm_test_dataset/mock_dexcom_server.py            # port 8473
    DEXCOM_MOCK_STREAM=0 python .../mock_dexcom_server.py             # full range
"""

from __future__ import annotations

import csv
import json
import os
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

QUERY_DT_FORMAT = "%Y-%m-%dT%H:%M:%S"


def _trend(delta_mg_dl_per_5min: float) -> str:
    d = delta_mg_dl_per_5min
    if d >= 15:
        return "doubleUp"
    if d >= 10:
        return "singleUp"
    if d >= 5:
        return "fortyFiveUp"
    if d <= -15:
        return "doubleDown"
    if d <= -10:
        return "singleDown"
    if d <= -5:
        return "fortyFiveDown"
    return "flat"


def load_egvs(csv_path: Path, source_tz: str) -> list[dict[str, object]]:
    """Read the importer-format CSV and build Dexcom v3 EGV records (UTC systemTime)."""
    zone = ZoneInfo(source_tz)
    records: list[dict[str, object]] = []
    prev_by_device: dict[str, float] = {}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            local_naive = datetime.fromisoformat(row["timestamp"])
            system_dt = local_naive.replace(tzinfo=zone).astimezone(timezone.utc).replace(tzinfo=None)
            value = round(float(row["value"]))
            device = row.get("device_id") or "mock-sensor"
            prev = prev_by_device.get(device)
            trend = _trend(value - prev) if prev is not None else "flat"
            prev_by_device[device] = value
            records.append(
                {
                    "_dt": system_dt,  # internal sort/filter key (stripped on output)
                    "recordId": row.get("record_id") or f"egv-{len(records):06d}",
                    "systemTime": system_dt.strftime(QUERY_DT_FORMAT),
                    "displayTime": local_naive.strftime(QUERY_DT_FORMAT),
                    "transmitterId": device,
                    "transmitterGeneration": "g6",
                    "displayDevice": "iPhone",
                    "unit": "mg/dL",
                    "rateUnit": "mg/dL/min",
                    "value": value,
                    "trend": trend,
                    "trendRate": None,
                    "status": None,
                }
            )
    records.sort(key=lambda r: r["_dt"])  # chronological
    return records


# Local-time event schedule aligned to the EGV meal spikes (07:30 / 12:30 / 18:45)
# so carbs/insulin/exercise actually explain the curve. Exercise on Mon/Wed/Fri.
_MEAL_EVENTS = (
    (7, 30, "carbs", 45, "grams"),
    (7, 30, "insulin", 6, "units"),
    (12, 30, "carbs", 60, "grams"),
    (12, 30, "insulin", 7, "units"),
    (18, 45, "carbs", 55, "grams"),
    (18, 45, "insulin", 6, "units"),
)


def build_events(start_dt: datetime, end_dt: datetime, source_tz: str) -> list[dict[str, object]]:
    """Synthesize Dexcom v3 event records (carbs/insulin/exercise) over the
    dataset window, in local time, converted to UTC systemTime."""
    zone = ZoneInfo(source_tz)
    # Walk local calendar days spanned by the (UTC) data window.
    first_local = start_dt.replace(tzinfo=timezone.utc).astimezone(zone)
    last_local = end_dt.replace(tzinfo=timezone.utc).astimezone(zone)
    events: list[dict[str, object]] = []
    day = first_local.replace(hour=0, minute=0, second=0, microsecond=0)
    idx = 0
    while day.date() <= last_local.date():
        schedule = list(_MEAL_EVENTS)
        if day.weekday() in (0, 2, 4):  # Mon/Wed/Fri
            schedule.append((17, 0, "exercise", 30, "minutes"))
        for hour, minute, etype, value, unit in schedule:
            local = day.replace(hour=hour, minute=minute)
            system_dt = local.astimezone(timezone.utc).replace(tzinfo=None)
            if not (start_dt <= system_dt <= end_dt):
                continue
            events.append(
                {
                    "_dt": system_dt,
                    "recordId": f"evt-{etype}-{idx:05d}",
                    "systemTime": system_dt.strftime(QUERY_DT_FORMAT),
                    "displayTime": local.replace(tzinfo=None).strftime(QUERY_DT_FORMAT),
                    "eventType": etype,
                    "eventSubType": None,
                    "value": str(value),
                    "unit": unit,
                    "eventStatus": "created",
                }
            )
            idx += 1
        day = day + timedelta(days=1)
    events.sort(key=lambda r: r["_dt"])
    return events


class MockState:
    def __init__(
        self,
        records: list[dict[str, object]],
        *,
        stream: bool,
        step_days: int,
        events: list[dict[str, object]] | None = None,
    ) -> None:
        self.records = records
        self.events = events or []
        self.stream = stream
        self.step_days = max(1, step_days)
        self.polls = 0
        self.lock = threading.Lock()
        self.start_dt: datetime = records[0]["_dt"] if records else datetime.now(timezone.utc).replace(tzinfo=None)
        self.end_dt: datetime = records[-1]["_dt"] if records else self.start_dt

    def visible_end(self) -> datetime:
        if not self.stream:
            return self.end_dt
        with self.lock:
            self.polls += 1
            polls = self.polls
        edge = self.start_dt + timedelta(days=self.step_days * polls)
        return min(self.end_dt, edge)

    def reset(self) -> None:
        with self.lock:
            self.polls = 0


def _make_handler(state: MockState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # quiet
            return

        def _send(self, code: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if path == "/v3/oauth2/token":
                self._send(
                    200,
                    {
                        "access_token": "mock-access-token",
                        "refresh_token": "mock-refresh-token",
                        "expires_in": 7200,
                        "token_type": "Bearer",
                        "scope": "offline_access",
                    },
                )
                return
            self._send(404, {"error": "not_found", "path": path})

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            if path == "/v3/users/self/dataRange":
                if query.get("reset"):
                    state.reset()
                end = state.visible_end()
                node = lambda dt: {
                    "systemTime": dt.strftime(QUERY_DT_FORMAT),
                    "displayTime": dt.strftime(QUERY_DT_FORMAT),
                }
                self._send(
                    200,
                    {
                        "recordType": "egv",
                        "egvs": {"start": node(state.start_dt), "end": node(end)},
                        "events": {"start": node(state.start_dt), "end": node(end)},
                        "calibrations": {"start": node(state.start_dt), "end": node(end)},
                    },
                )
                return
            if path == "/v3/users/self/egvs":
                self._send(200, {"recordType": "egv", "recordVersion": "3.0",
                                 "userId": "mock-user", "records": self._slice(query)})
                return
            if path == "/v3/users/self/events":
                self._send(200, {"recordType": "event", "recordVersion": "3.0",
                                 "userId": "mock-user", "records": self._slice(query, state.events)})
                return
            self._send(404, {"error": "not_found", "path": path})

        def _slice(self, query: dict, source: list | None = None) -> list[dict]:
            rows = state.records if source is None else source
            start = _parse_q(query.get("startDate", [None])[0])
            end = _parse_q(query.get("endDate", [None])[0])
            # Dexcom treats the window as inclusive of endDate. Using a half-open
            # (dt < end) filter here would silently drop the single reading sitting
            # exactly on dataRange.end (the last point of a fixed dataset), so the
            # range is inclusive on both ends. Chunked pulls then overlap by one
            # record at each internal boundary, which the repository's
            # UNIQUE(user_id, timestamp, source) dedup absorbs.
            out = []
            for rec in rows:
                dt = rec["_dt"]
                if start is not None and dt < start:
                    continue
                if end is not None and dt > end:
                    continue
                out.append({k: v for k, v in rec.items() if k != "_dt"})
            return out

    return Handler


def _parse_q(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, QUERY_DT_FORMAT)
    except ValueError:
        return None


def main() -> None:
    here = Path(__file__).parent
    csv_path = Path(os.getenv("DEXCOM_MOCK_CSV", str(here / "cgm_3x14.csv")))
    port = int(os.getenv("DEXCOM_MOCK_PORT", "8473"))
    source_tz = os.getenv("DEXCOM_MOCK_TZ", "Asia/Shanghai")
    stream = (os.getenv("DEXCOM_MOCK_STREAM", "1").strip().lower() in {"1", "true", "yes", "on"})
    step_days = int(os.getenv("DEXCOM_MOCK_STEP_DAYS", "7"))

    records = load_egvs(csv_path, source_tz)
    events = build_events(records[0]["_dt"], records[-1]["_dt"], source_tz) if records else []
    state = MockState(records, stream=stream, step_days=step_days, events=events)
    server = ThreadingHTTPServer(("127.0.0.1", port), _make_handler(state))
    print(
        f"mock-dexcom: {len(records)} EGVs + {len(events)} events from {csv_path.name} "
        f"[{state.start_dt} .. {state.end_dt} UTC] on http://127.0.0.1:{port} "
        f"(stream={'on' if stream else 'off'}, step={step_days}d)",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
