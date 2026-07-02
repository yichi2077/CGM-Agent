"""Serve the default synthetic CGM CSV as an xDrip/Nightscout-style HTTP feed."""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo


TREND_TO_XDRIP = {
    "rising_fast": "DoubleUp",
    "rising": "SingleUp",
    "stable": "Flat",
    "falling": "SingleDown",
    "falling_fast": "DoubleDown",
}


@dataclass(frozen=True)
class FeedPoint:
    local_time: datetime
    value: float
    unit: str
    device_id: str
    record_id: str
    trend: str
    status: str
    artifact: str
    event_ids: str


class VirtualCGMFeedState:
    def __init__(
        self,
        points: list[FeedPoint],
        *,
        timezone_name: str,
        emit_interval_minutes: int,
        start_index: int = 0,
    ) -> None:
        if emit_interval_minutes < 1:
            raise ValueError("emit_interval_minutes must be positive")
        self.timezone = ZoneInfo(timezone_name)
        self.points = _select_emit_points(points, emit_interval_minutes)
        self.cursor = max(0, start_index)

    def next_payload(self, count: int) -> list[dict[str, Any]]:
        count = max(1, count)
        selected = self.points[self.cursor:self.cursor + count]
        self.cursor += len(selected)
        return [self._to_source_row(point) for point in selected]

    def _to_source_row(self, point: FeedPoint) -> dict[str, Any]:
        aware = point.local_time.replace(tzinfo=self.timezone).astimezone(timezone.utc)
        return {
            "_id": point.record_id,
            "sgv": point.value,
            "date": int(aware.timestamp() * 1000),
            "dateString": aware.isoformat().replace("+00:00", "Z"),
            "direction": TREND_TO_XDRIP.get(point.trend, "Flat"),
            "device": point.device_id,
            "unit": point.unit,
            "status": point.status or None,
            "artifact": point.artifact or None,
            "eventIds": [item for item in point.event_ids.split(";") if item],
        }


def load_points(path: str | Path) -> list[FeedPoint]:
    points: list[FeedPoint] = []
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            points.append(
                FeedPoint(
                    local_time=datetime.fromisoformat(row["timestamp"]),
                    value=float(row["value"]),
                    unit=row.get("unit") or "mg/dL",
                    device_id=row.get("device_id") or "virtual-cgm",
                    record_id=row.get("record_id") or row["timestamp"],
                    trend=row.get("trend") or "stable",
                    status=row.get("status") or "",
                    artifact=row.get("artifact") or "",
                    event_ids=row.get("event_ids") or "",
                )
            )
    return sorted(points, key=lambda point: point.local_time)


def build_handler(state: VirtualCGMFeedState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path not in {"/sgv.json", "/api/v1/entries/sgv.json", "/"}:
                self.send_response(404)
                self.end_headers()
                return
            query = parse_qs(parsed.query)
            count = _parse_count(query.get("count", ["1"])[0])
            payload = state.next_payload(count)
            body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

    return Handler


def _select_emit_points(points: list[FeedPoint], emit_interval_minutes: int) -> list[FeedPoint]:
    if not points:
        return []
    first = points[0].local_time
    selected: list[FeedPoint] = []
    for point in points:
        delta_minutes = int((point.local_time - first).total_seconds() // 60)
        if delta_minutes % emit_interval_minutes == 0:
            selected.append(point)
    return selected


def _parse_count(value: str) -> int:
    try:
        return max(1, int(value))
    except ValueError:
        return 1


def main() -> None:
    here = Path(__file__).parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", default=str(here / "cgm_14d_1min.csv"))
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument("--emit-interval-min", type=int, default=5)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=17580)
    parser.add_argument("--start-index", type=int, default=0)
    args = parser.parse_args()

    state = VirtualCGMFeedState(
        load_points(args.csv),
        timezone_name=args.timezone,
        emit_interval_minutes=args.emit_interval_min,
        start_index=args.start_index,
    )
    server = ThreadingHTTPServer((args.host, args.port), build_handler(state))
    print(
        f"Serving {len(state.points)} virtual CGM points from {args.csv} "
        f"at http://{args.host}:{args.port}/sgv.json"
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
