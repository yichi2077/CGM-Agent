"""Continuously poll a virtual CGM HTTP feed for simulation runs.

This is a local runner for the 14-day Hermes simulation. It does not own product
policy or scheduling; it repeatedly calls the existing SourcePollService so data
lands in the same SQLite store used by Hermes tools.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Literal


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from hermes_cgm_agent.config import resolve_database_path
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.sources import SourcePollConfig, SourcePollService
from hermes_cgm_agent.storage.sqlite import SQLiteStore


SourceKind = Literal["xdrip", "juggluco", "nightscout"]


@dataclass(frozen=True)
class AutoPollSummary:
    status: str
    database_path: str
    user_id: str
    poll_count: int
    inserted_count: int
    duplicate_count: int
    issue_count: int
    detected_event_inserted: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def run_auto_poll(
    *,
    db_path: Path,
    user_id: str,
    kind: SourceKind,
    url: str,
    count: int,
    source: str | None,
    expected_interval_minutes: int,
    interval_seconds: float,
    duration_hours: float | None = None,
    max_polls: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
    emit_json: bool = True,
) -> AutoPollSummary:
    if max_polls is None and duration_hours is None:
        raise ValueError("set either max_polls or duration_hours")
    if interval_seconds < 0:
        raise ValueError("interval_seconds must be non-negative")

    store = SQLiteStore(db_path)
    store.initialize()
    service = SourcePollService(
        repository=SQLiteCGMRepository(store),
        config=SourcePollConfig(expected_interval_minutes=expected_interval_minutes),
    )

    deadline = None if duration_hours is None else time.monotonic() + (duration_hours * 3600)
    poll_count = 0
    inserted = 0
    duplicate = 0
    issues = 0
    detected_event_inserted = 0

    while True:
        if max_polls is not None and poll_count >= max_polls:
            break
        if deadline is not None and time.monotonic() >= deadline:
            break

        result = service.poll(
            user_id=user_id,
            kind=kind,
            url=url,
            count=count,
            source=source,
        )
        poll_count += 1
        inserted += result.inserted_count
        duplicate += result.duplicate_count
        issues += result.issue_count
        detected_event_inserted += result.detected_event_inserted

        if emit_json:
            body = result.to_dict()
            body["database_path"] = str(db_path)
            body["poll_index"] = poll_count
            print(json.dumps(body, ensure_ascii=False, sort_keys=True), flush=True)

        if max_polls is not None and poll_count >= max_polls:
            break
        if deadline is not None and time.monotonic() >= deadline:
            break
        if interval_seconds:
            sleep(interval_seconds)

    return AutoPollSummary(
        status="ok",
        database_path=str(db_path),
        user_id=user_id,
        poll_count=poll_count,
        inserted_count=inserted,
        duplicate_count=duplicate,
        issue_count=issues,
        detected_event_inserted=detected_event_inserted,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", default="demo-prediabetes-user")
    parser.add_argument("--kind", choices=["xdrip", "juggluco", "nightscout"], default="xdrip")
    parser.add_argument("--url", default="http://127.0.0.1:17580")
    parser.add_argument("--count", type=int, default=12)
    parser.add_argument("--source", default="virtual:aidex")
    parser.add_argument("--expected-interval-min", type=int, default=5)
    parser.add_argument("--interval-sec", type=float, default=None)
    parser.add_argument("--interval-min", type=float, default=5.0)
    parser.add_argument("--duration-hours", type=float, default=None)
    parser.add_argument("--max-polls", type=int, default=None)
    parser.add_argument("--db-path", default=None)
    args = parser.parse_args()

    interval_seconds = (
        args.interval_sec if args.interval_sec is not None else args.interval_min * 60
    )
    db_path = Path(args.db_path).expanduser().resolve() if args.db_path else resolve_database_path()
    summary = run_auto_poll(
        db_path=db_path,
        user_id=args.user_id,
        kind=args.kind,
        url=args.url,
        count=args.count,
        source=args.source,
        expected_interval_minutes=args.expected_interval_min,
        interval_seconds=interval_seconds,
        duration_hours=args.duration_hours,
        max_polls=args.max_polls,
    )
    print(json.dumps(summary.to_dict(), ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
