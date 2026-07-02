#!/usr/bin/env python3
"""External CGM Data Receiver — standalone background process.

Reads glucose values from the 14-day synthetic CGM CSV (1-min granularity),
emits one reading every 5 minutes using current UTC time (NOT the CSV
timestamp), and persists data through SourcePollService into the shared
Hermes CGM Agent SQLite database.

Key differences from simulation_tick.py:
  - Does NOT require Hermes cron — runs as a standalone Python process.
  - Injects current UTC time into every reading instead of using CSV timestamps.
  - Tracks emission progress in a JSON state file (survives process restarts).
  - Sends WeChat notifications on failure via `hermes send --to weixin`.

Usage:
  python cgm_receiver.py                        # defaults
  python cgm_receiver.py --once                  # emit one point and exit
  python cgm_receiver.py --dry-run               # validate without writing
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path setup — replicate what simulation_tick.py does
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parents[1] if _HERE.name == "cgm_test_dataset" else _HERE
_SRC_ROOT = _PROJECT_ROOT / "src"

for _p in (_HERE, str(_SRC_ROOT)):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from hermes_cgm_agent.config import resolve_database_path
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.sources import SourcePollConfig, SourcePollService
from hermes_cgm_agent.services.sources.models import SourceKind
from hermes_cgm_agent.storage.sqlite import SQLiteStore

# Trend mapping (same as virtual_cgm_feed.py)
TREND_TO_XDRIP: dict[str, str] = {
    "rising_fast": "DoubleUp",
    "rising": "SingleUp",
    "stable": "Flat",
    "falling": "SingleDown",
    "falling_fast": "DoubleDown",
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG = logging.getLogger("cgm_receiver")
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt="%Y-%m-%dT%H:%M:%S")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FeedPoint:
    local_time: datetime
    value: float
    unit: str
    device_id: str
    record_id: str
    trend: str


@dataclass
class ReceiverState:
    index: int
    total_points: int
    last_tick_utc: str | None
    csv_path: str


# ---------------------------------------------------------------------------
# CSV loading & 5-minute filtering (same logic as VirtualCGMFeedState)
# ---------------------------------------------------------------------------
def load_points(csv_path: str | Path) -> list[FeedPoint]:
    points: list[FeedPoint] = []
    with Path(csv_path).open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            points.append(
                FeedPoint(
                    local_time=datetime.fromisoformat(row["timestamp"]),
                    value=float(row["value"]),
                    unit=row.get("unit") or "mg/dL",
                    device_id=row.get("device_id") or "virtual-cgm",
                    record_id=row.get("record_id") or row["timestamp"],
                    trend=row.get("trend") or "stable",
                )
            )
    return sorted(points, key=lambda point: point.local_time)


def select_emit_points(points: list[FeedPoint], interval_minutes: int) -> list[FeedPoint]:
    """Filter points to those at *interval_minutes* boundaries from the first."""
    if not points:
        return []
    first = points[0].local_time
    selected: list[FeedPoint] = []
    for point in points:
        delta_minutes = int((point.local_time - first).total_seconds() // 60)
        if delta_minutes % interval_minutes == 0:
            selected.append(point)
    return selected


# ---------------------------------------------------------------------------
# Custom HTTP client — returns payload WITHOUT making real HTTP requests
# ---------------------------------------------------------------------------
class VirtualCGMSourceClient:
    """Drop-in replacement for HTTPSourceClient.

    Instead of fetching from a URL, returns a pre-constructed payload where
    the timestamp is *current UTC time* (not the CSV timestamp).  This
    satisfies the requirement that every reading uses the real ingestion time.
    """

    def __init__(self, point: FeedPoint) -> None:
        self._point = point

    def fetch_json(self, *, url: str, kind: SourceKind, count: int) -> tuple[str, Any]:
        now_utc = datetime.now(timezone.utc)
        payload = [
            {
                "_id": self._point.record_id,
                "sgv": self._point.value,
                "date": int(now_utc.timestamp() * 1000),
                "dateString": now_utc.isoformat().replace("+00:00", "Z"),
                "direction": TREND_TO_XDRIP.get(self._point.trend, "Flat"),
                "device": self._point.device_id,
                "unit": self._point.unit,
            }
        ]
        return url, payload


# ---------------------------------------------------------------------------
# State file persistence (atomic writes)
# ---------------------------------------------------------------------------
def load_state(state_path: Path) -> ReceiverState:
    if state_path.exists():
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            return ReceiverState(
                index=int(data.get("index", 0)),
                total_points=int(data.get("total_points", 0)),
                last_tick_utc=data.get("last_tick_utc"),
                csv_path=data.get("csv_path", ""),
            )
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            LOG.warning("Corrupt state file, resetting: %s", exc)
    return ReceiverState(index=0, total_points=0, last_tick_utc=None, csv_path="")


def save_state(state_path: Path, state: ReceiverState) -> None:
    data = {
        "index": state.index,
        "total_points": state.total_points,
        "last_tick_utc": state.last_tick_utc,
        "csv_path": state.csv_path,
    }
    # Atomic write: temp file + rename
    tmp_fd, tmp_name = tempfile.mkstemp(
        suffix=".json", prefix=".cgm_receiver_state.", dir=str(state_path.parent)
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_name, str(state_path))
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# WeChat notification
# ---------------------------------------------------------------------------
WECHAT_TARGET = "weixin:o9cq80yxtMVOK0GbUpXeZ7dDrYQI@im.wechat"
NOTIFY_MAX_RETRIES = 3
NOTIFY_COOLDOWN_SEC = 300  # 5 min — iLink rate-limit requires ≥180s quiet


def _find_hermes_exe() -> str | None:
    """Locate the hermes CLI binary."""
    # Try common locations on Windows
    candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\hermes.exe"),
        os.path.expanduser(r"~\.hermes\bin\hermes.exe"),
        "hermes",  # fallback: hope it's on PATH
    ]
    for c in candidates:
        if os.path.isfile(c) or c == "hermes":
            return c
    # Check PATH
    import shutil
    found = shutil.which("hermes")
    return found


def send_wechat_notification(message: str, subject: str = "[CGM Receiver]") -> bool:
    """Send a WeChat notification via `hermes send`.

    Returns True on success, False on failure.
    Implements retry with 5-min cooldown between attempts.
    """
    hermes_exe = _find_hermes_exe()
    if not hermes_exe:
        LOG.error("Cannot find hermes CLI — notification skipped")
        return False

    for attempt in range(1, NOTIFY_MAX_RETRIES + 1):
        LOG.info("WeChat notification attempt %d/%d", attempt, NOTIFY_MAX_RETRIES)
        try:
            result = subprocess.run(
                [hermes_exe, "send", "--to", WECHAT_TARGET, "--subject", subject, message],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                LOG.info("WeChat notification sent successfully")
                return True
            LOG.warning(
                "hermes send failed (exit %d): %s",
                result.returncode,
                result.stderr.strip() or result.stdout.strip(),
            )
        except subprocess.TimeoutExpired:
            LOG.warning("hermes send timed out (attempt %d)", attempt)
        except FileNotFoundError:
            LOG.error("hermes executable not found at %s", hermes_exe)
            return False
        except Exception:
            LOG.exception("Unexpected error calling hermes send")

        if attempt < NOTIFY_MAX_RETRIES:
            LOG.info("Cooling down %ds before notification retry...", NOTIFY_COOLDOWN_SEC)
            time.sleep(NOTIFY_COOLDOWN_SEC)

    LOG.error("All %d WeChat notification attempts failed", NOTIFY_MAX_RETRIES)
    return False


# ---------------------------------------------------------------------------
# Core: emit one tick
# ---------------------------------------------------------------------------
def emit_tick(
    *,
    db_path: Path,
    user_id: str,
    source: str,
    kind: SourceKind,
    point: FeedPoint,
    expected_interval_minutes: int,
) -> dict[str, Any]:
    """Emit a single CGM reading to the database via SourcePollService."""
    store = SQLiteStore(db_path)
    store.initialize()
    repository = SQLiteCGMRepository(store)
    client = VirtualCGMSourceClient(point)
    service = SourcePollService(
        repository=repository,
        client=client,
        config=SourcePollConfig(expected_interval_minutes=expected_interval_minutes),
    )
    # The URL is never actually fetched — our VirtualCGMSourceClient bypasses HTTP.
    dummy_url = "http://127.0.0.1:0/virtual"
    result = service.poll(
        user_id=user_id,
        kind=kind,
        url=dummy_url,
        count=1,
        source=source,
    )
    return result.to_dict()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--user-id",
        default="demo-prediabetes-14d-v2",
        help="User ID for glucose points (default: demo-prediabetes-14d-v2)",
    )
    parser.add_argument(
        "--source",
        default="virtual:aidex-v2",
        help="Source label (default: virtual:aidex-v2)",
    )
    parser.add_argument(
        "--kind",
        choices=["xdrip", "juggluco", "nightscout"],
        default="xdrip",
        help="Source kind / payload format (default: xdrip)",
    )
    parser.add_argument(
        "--csv",
        default=str(_HERE / "cgm_14d_1min.csv"),
        help="Path to the CGM CSV dataset",
    )
    parser.add_argument(
        "--emit-interval-min",
        type=int,
        default=5,
        help="Minutes between emitted points (default: 5)",
    )
    parser.add_argument(
        "--expected-interval-min",
        type=int,
        default=5,
        help="Expected interval for event detection (default: 5)",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="SQLite DB path (default: auto-detect via resolve_database_path)",
    )
    parser.add_argument(
        "--state-file",
        default=None,
        help="Path to the progress state file (default: <cwd>/.cgm_receiver_state.json)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Emit exactly one reading and exit (good for testing)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate setup and print the next point, but do NOT write to DB",
    )
    parser.add_argument(
        "--no-notify",
        action="store_true",
        help="Skip WeChat notifications on failure (for testing)",
    )
    args = parser.parse_args()

    # Resolve paths
    csv_path = Path(args.csv).expanduser().resolve()
    db_path = (
        Path(args.db_path).expanduser().resolve()
        if args.db_path
        else resolve_database_path()
    )
    state_path = (
        Path(args.state_file).expanduser().resolve()
        if args.state_file
        else (Path.cwd() / ".cgm_receiver_state.json")
    )

    LOG.info("=== CGM Receiver starting ===")
    LOG.info("  CSV:      %s", csv_path)
    LOG.info("  DB:       %s", db_path)
    LOG.info("  State:    %s", state_path)
    LOG.info("  User ID:  %s", args.user_id)
    LOG.info("  Source:   %s", args.source)
    LOG.info("  Interval: %d min", args.emit_interval_min)

    # ── 1. Load & filter points ──
    if not csv_path.exists():
        LOG.error("CSV file not found: %s", csv_path)
        return 2

    all_points = load_points(csv_path)
    emit_points = select_emit_points(all_points, args.emit_interval_min)
    total = len(emit_points)
    LOG.info("Loaded %d raw points → %d emit points (every %d min)", len(all_points), total, args.emit_interval_min)

    if total == 0:
        LOG.error("No emit points after filtering — check CSV and interval")
        return 2

    # ── 2. Restore or initialise state ──
    state = load_state(state_path)
    # If the CSV path changed or total_points mismatch, reset state
    if state.csv_path != str(csv_path) or state.total_points != total:
        LOG.info("CSV changed or new run — resetting state to index 0")
        state = ReceiverState(index=0, total_points=total, last_tick_utc=None, csv_path=str(csv_path))

    # ── 3. Dry-run mode ──
    if args.dry_run:
        idx = state.index
        if idx >= total:
            print(json.dumps({"status": "complete", "index": idx, "total": total}))
            return 0
        point = emit_points[idx]
        now_utc = datetime.now(timezone.utc)
        print(json.dumps({
            "status": "dry_run",
            "index": idx,
            "total": total,
            "csv_timestamp": point.local_time.isoformat(),
            "would_emit_utc": now_utc.isoformat(),
            "value": point.value,
            "unit": point.unit,
            "trend": point.trend,
            "device_id": point.device_id,
        }, ensure_ascii=False, indent=2))
        return 0

    # ── 4. Main emit loop ──
    consecutive_failures = 0
    notified_current_failure = False  # only notify once per failure burst

    while True:
        # Check if we've exhausted the dataset
        if state.index >= total:
            LOG.info("All %d points emitted — receiver complete", total)
            send_wechat_notification(
                f"CGM Receiver: all {total} points emitted. Receiver exiting normally.",
                subject="[CGM Receiver] Complete",
            )
            save_state(state_path, state)
            return 0

        point = emit_points[state.index]
        now_utc = datetime.now(timezone.utc)

        try:
            LOG.info(
                "Tick %d/%d | value=%.1f %s | CSV ts=%s | emit UTC=%s",
                state.index + 1,
                total,
                point.value,
                point.unit,
                point.local_time.isoformat(),
                now_utc.isoformat(),
            )

            result = emit_tick(
                db_path=db_path,
                user_id=args.user_id,
                source=args.source,
                kind=args.kind,
                point=point,
                expected_interval_minutes=args.expected_interval_min,
            )

            # Success
            state.index += 1
            state.last_tick_utc = now_utc.isoformat()
            save_state(state_path, state)
            consecutive_failures = 0
            notified_current_failure = False

            LOG.info(
                "  → inserted=%d duplicate=%d issues=%d events=%d",
                result.get("inserted_count", 0),
                result.get("duplicate_count", 0),
                result.get("issue_count", 0),
                result.get("detected_event_inserted", 0),
            )

        except Exception:
            consecutive_failures += 1
            error_msg = traceback.format_exc()
            LOG.error("Tick %d FAILED (consecutive failures: %d):\n%s",
                       state.index + 1, consecutive_failures, error_msg)

            # Notify on first failure of a burst (not on every retry)
            if not notified_current_failure and not args.no_notify:
                truncated = error_msg[-1500:] if len(error_msg) > 1500 else error_msg
                send_wechat_notification(
                    f"CGM Receiver failed at tick {state.index + 1}/{total} "
                    f"(consecutive failures: {consecutive_failures})\n\n{truncated}",
                    subject="[CGM Receiver] FAILURE",
                )
                notified_current_failure = True

            # Wait 5 minutes before retrying (iLink cooldown constraint)
            LOG.info("Waiting %ds before retry...", NOTIFY_COOLDOWN_SEC)
            time.sleep(NOTIFY_COOLDOWN_SEC)
            continue

        # ── Done after --once ──
        if args.once:
            LOG.info("--once flag set: exiting after single tick")
            return 0

        # ── Wait for next interval ──
        LOG.info("Sleeping %d seconds until next tick...", args.emit_interval_min * 60)
        time.sleep(args.emit_interval_min * 60)


if __name__ == "__main__":
    raise SystemExit(main())
