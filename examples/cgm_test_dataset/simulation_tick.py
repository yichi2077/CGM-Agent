"""Run one resumable real-time virtual CGM simulation tick.

The 14-day simulation should survive shell exits, Codex restarts, and machine
sleep. This one-shot runner derives the next virtual feed index from the Hermes
SQLite database, serves that point through the same local HTTP feed adapter, and
then calls SourcePollService so ingestion still exercises the source-poll path.
Schedule it every 5 minutes for a real-time 14-day run.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Literal


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from hermes_cgm_agent.config import resolve_database_path
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.sources import SourcePollConfig, SourcePollService
from hermes_cgm_agent.storage.sqlite import SQLiteStore
from virtual_cgm_feed import VirtualCGMFeedState, build_handler, load_points


SourceKind = Literal["xdrip", "juggluco", "nightscout"]


@dataclass(frozen=True)
class SimulationTickResult:
    status: str
    database_path: str
    user_id: str
    source: str
    start_index: int
    total_emit_points: int
    remaining_before_tick: int
    inserted_count: int
    duplicate_count: int
    issue_count: int
    detected_event_inserted: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _existing_virtual_point_count(
    *,
    store: SQLiteStore,
    user_id: str,
    source: str,
) -> int:
    with store.connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM glucose_points WHERE user_id = ? AND source = ?",
            (user_id, source),
        ).fetchone()
    return int(row[0] if row else 0)


def run_simulation_tick(
    *,
    db_path: Path,
    user_id: str,
    kind: SourceKind,
    source: str,
    csv_path: Path,
    timezone_name: str,
    emit_interval_minutes: int,
    expected_interval_minutes: int,
    count: int = 1,
    host: str = "127.0.0.1",
    port: int = 0,
    received_at: datetime | None = None,
) -> SimulationTickResult:
    store = SQLiteStore(db_path)
    store.initialize()
    repository = SQLiteCGMRepository(store)
    start_index = _existing_virtual_point_count(store=store, user_id=user_id, source=source)

    state = VirtualCGMFeedState(
        load_points(csv_path),
        timezone_name=timezone_name,
        emit_interval_minutes=emit_interval_minutes,
        start_index=start_index,
    )
    total = len(state.points)
    remaining = max(0, total - start_index)
    if remaining <= 0:
        return SimulationTickResult(
            status="complete",
            database_path=str(db_path),
            user_id=user_id,
            source=source,
            start_index=start_index,
            total_emit_points=total,
            remaining_before_tick=0,
            inserted_count=0,
            duplicate_count=0,
            issue_count=0,
            detected_event_inserted=0,
        )

    server = ThreadingHTTPServer((host, port), build_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        service = SourcePollService(
            repository=repository,
            config=SourcePollConfig(expected_interval_minutes=expected_interval_minutes),
        )
        result = service.poll(
            user_id=user_id,
            kind=kind,
            url=f"http://{host}:{server.server_port}",
            count=min(count, remaining),
            source=source,
            received_at=received_at or datetime.now(timezone.utc),
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    return SimulationTickResult(
        status=result.to_dict()["status"],
        database_path=str(db_path),
        user_id=user_id,
        source=source,
        start_index=start_index,
        total_emit_points=total,
        remaining_before_tick=remaining,
        inserted_count=result.inserted_count,
        duplicate_count=result.duplicate_count,
        issue_count=result.issue_count,
        detected_event_inserted=result.detected_event_inserted,
    )


def main() -> int:
    here = Path(__file__).parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", default="demo-prediabetes-14d")
    parser.add_argument("--kind", choices=["xdrip", "juggluco", "nightscout"], default="xdrip")
    parser.add_argument("--source", default="virtual:aidex")
    parser.add_argument("--csv", default=str(here / "cgm_14d_1min.csv"))
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument("--emit-interval-min", type=int, default=5)
    parser.add_argument("--expected-interval-min", type=int, default=5)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--db-path", default=None)
    args = parser.parse_args()

    db_path = Path(args.db_path).expanduser().resolve() if args.db_path else resolve_database_path()
    result = run_simulation_tick(
        db_path=db_path,
        user_id=args.user_id,
        kind=args.kind,
        source=args.source,
        csv_path=Path(args.csv).expanduser().resolve(),
        timezone_name=args.timezone,
        emit_interval_minutes=args.emit_interval_min,
        expected_interval_minutes=args.expected_interval_min,
        count=args.count,
        host=args.host,
        port=args.port,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if result.status in {"ok", "complete"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
