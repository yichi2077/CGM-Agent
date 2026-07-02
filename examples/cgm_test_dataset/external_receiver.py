#!/usr/bin/env python3
"""
External CGM Data Receiver for 14-Day Virtual Simulation.

This script runs INDEPENDENTLY (not as a Hermes cron job).
Every 5 minutes it extracts one glucose value from the CSV,
assigns the CURRENT UTC TIMESTAMP (not CSV timestamp),
and writes it to the Hermes CGM database via SourcePollService.

Architecture:
  CSV(1-min values) → every 5 min → temp HTTP → SourcePollService → app.db
  └─ 5-min timer ─┘                 └─ uses current UTC time ─┘

Usage:
  python external_receiver.py --csv cgm_14d_1min.csv --interval-min 5
  python external_receiver.py --csv cgm_14d_1min.csv --interval-sec 300
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

# ── Bootstrap project paths ──────────────────────────────────────────
HERE = Path(__file__).resolve().parent
PROJECT_ROOT = Path(os.environ.get(
    "CGM_PROJECT_ROOT",
    "E:/字幕组测试/CGM-Agent/hermes-cgm-agent-latest"
))
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from hermes_cgm_agent.config import resolve_database_path
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.sources import SourcePollService
from hermes_cgm_agent.storage.sqlite import SQLiteStore

# ── Configuration ────────────────────────────────────────────────────
USER_ID = "demo-prediabetes-14d-v2"
SOURCE = "virtual:aidex-v2"
DEVICE_ID = "VIRTUAL-AIDEX-X-001"
UNIT = "mg/dL"


@dataclass
class ReceiverState:
    """Tracks receiver progress using SQLite row count as index."""
    points: list[dict[str, Any]]
    user_id: str = USER_ID
    source: str = SOURCE
    db_path: Optional[str] = None
    _store: Optional[SQLiteStore] = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def current_index(self) -> int:
        """Derive next CSV index from SQLite row count (direct query, same as simulation_tick.py)."""
        if self._store is None:
            self._store = SQLiteStore(self.db_path or resolve_database_path())
        with self._store.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM glucose_points WHERE user_id = ? AND source = ?",
                (self.user_id, self.source),
            ).fetchone()
        return int(row[0]) if row else 0

    def total_points(self) -> int:
        return len(self.points)


def load_points(csv_path: str) -> list[dict[str, Any]]:
    """Load CSV values (discard CSV timestamps — we assign real time later)."""
    points = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            points.append({
                "value": float(row["value"]),
                "unit": row.get("unit", "mg/dL"),
                "trend": row.get("trend", "stable"),
                "artifact": row.get("artifact", ""),
            })
    return points


def build_handler(state: ReceiverState):
    """Create an HTTP handler that serves one point with a REAL timestamp."""

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path != "/sgv.json":
                self.send_error(404)
                return

            idx = state.current_index()
            if idx >= state.total_points():
                self.send_error(410, "No more data points")
                return

            pt = state.points[idx]
            now_utc = datetime.now(timezone.utc)

            entry = {
                "_id": f"{DEVICE_ID}-{idx:06d}",
                "sgv": pt["value"],
                "date": int(now_utc.timestamp() * 1000),
                "dateString": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "direction": pt["trend"].title(),
                "device": DEVICE_ID,
                "unit": pt["unit"],
                "status": pt["artifact"] if pt["artifact"] else None,
                "artifact": pt["artifact"] or None,
                "eventIds": [],
            }

            body = json.dumps([entry]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            pass  # suppress HTTP logs

    return _Handler


def poll_once(state: ReceiverState, host: str, port: int) -> dict:
    """Poll the temporary HTTP server to ingest 1 point."""
    store = state._store or SQLiteStore(state.db_path or resolve_database_path())
    repository = SQLiteCGMRepository(store)
    svc = SourcePollService(repository=repository)
    try:
        result = svc.poll(
            user_id=state.user_id,
            kind="xdrip",
            url=f"http://{host}:{port}/sgv.json",
            count=1,
            source=state.source,
        )
        return result.to_dict()
    except Exception as e:
        return {"status": "error", "error": str(e)}


def notify_wechat(message: str) -> bool:
    """Send a notification to WeChat via hermes send."""
    import subprocess
    try:
        result = subprocess.run(
            ["hermes", "send", "--to", "weixin", message],
            capture_output=True, text=True, timeout=15,
        )
        return result.returncode == 0
    except Exception:
        return False


def run_receiver(
    csv_path: str,
    interval_seconds: int = 300,
    host: str = "127.0.0.1",
    port: int = 0,
):
    """Main loop: poll one point every interval_seconds."""
    points = load_points(csv_path)
    total = len(points)
    db_path = resolve_database_path()
    state = ReceiverState(points=points, db_path=db_path)

    start_idx = state.current_index()
    print(f"[Receiver] CSV loaded: {total} points")
    print(f"[Receiver] DB current index: {start_idx}")
    print(f"[Receiver] Interval: {interval_seconds}s (≈{interval_seconds/60:.0f}min)")
    print(f"[Receiver] Will run until all {total} points are consumed")

    consecutive_errors = 0
    consumed = 0

    while True:
        idx = state.current_index()
        if idx >= total:
            print(f"[Receiver] All {total} points consumed. Done.")
            notify_wechat("📊 CGM接收器：全部数据点已消费完毕（{}点）。".format(total))
            break

        remaining = total - idx
        try:
            # Start a temporary HTTP server on a random port
            server = ThreadingHTTPServer(
                (host, port), build_handler(state)
            )
            actual_port = server.server_address[1]
            server_thread = threading.Thread(target=server.handle_request, daemon=True)
            server_thread.start()
            time.sleep(0.3)  # let server start

            # Poll the point
            result = poll_once(state, host, actual_port)
            server.server_close()

            inserted = result.get("inserted_count", 0)
            duplicate = result.get("duplicate_count", 0)
            status = result.get("status", "unknown")

            if status == "ok" and inserted > 0:
                consumed += 1
                pts_val = points[idx]["value"]
                print(f"[Receiver] tick ok — consumed {consumed}/{total} "
                      f"(DB idx={idx}, val={pts_val} mg/dL)")
                consecutive_errors = 0
            elif duplicate > 0:
                print(f"[Receiver] duplicate at idx={idx}, skipping")
                consecutive_errors = 0
            else:
                print(f"[Receiver] WARN: status={status}, result={result}")
                consecutive_errors += 1

            # Notify on persistent errors
            if consecutive_errors >= 3:
                msg = f"⚠️ CGM接收器：连续{consecutive_errors}次异常。最后状态={status}"
                print(f"[Receiver] {msg}")
                notify_wechat(msg)

        except Exception as e:
            consecutive_errors += 1
            print(f"[Receiver] ERROR: {e}")
            if consecutive_errors >= 3:
                notify_wechat(f"❌ CGM接收器：崩溃（连续{consecutive_errors}次异常）：{str(e)[:100]}")

        # Check if we have more points
        if state.current_index() >= total:
            continue  # loop back to the top to exit cleanly

        # Sleep the remaining time
        print(f"[Receiver] sleeping {interval_seconds}s... (next: idx={state.current_index()}/{total})")
        time.sleep(interval_seconds)


def main():
    parser = argparse.ArgumentParser(description="External CGM Data Receiver")
    parser.add_argument("--csv", required=True, help="Path to CSV file")
    parser.add_argument("--interval-min", type=int, default=5,
                        help="Polling interval in minutes (default=5)")
    parser.add_argument("--interval-sec", type=int, default=None,
                        help="Polling interval in seconds (overrides --interval-min)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="HTTP server port (0=auto)")
    args = parser.parse_args()

    interval = args.interval_sec or (args.interval_min * 60)

    run_receiver(
        csv_path=args.csv,
        interval_seconds=interval,
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
