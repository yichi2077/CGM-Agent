"""Windows-safe Hermes cron watchdog for newly detected CGM events.

The script is intentionally no-agent: an empty stdout means no delivery.  It
stores only the last delivered event id beside the configured database, so a
repeated 30-minute tick cannot send the same event twice.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_root = os.getenv("CGM_AGENT_PROJECT_ROOT")
if _root and str(Path(_root) / "src") not in sys.path:
    sys.path.insert(0, str(Path(_root) / "src"))

from hermes_cgm_agent.config import resolve_database_path
from hermes_cgm_agent.storage.sqlite import SQLiteStore


def main() -> int:
    user_id = os.getenv("CGM_AGENT_USER_ID", "demo-user").strip() or "demo-user"
    db = resolve_database_path(os.getenv("HERMES_HOME"))
    store = SQLiteStore(db)
    store.initialize()
    with store.connect() as conn:
        row = conn.execute(
            "SELECT event_id, event_type, ts_start FROM detected_glucose_events "
            "WHERE user_id = ? ORDER BY ts_start DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    if row is None:
        return 0
    state_path = db.parent / "event-monitor-state.json"
    previous = ""
    if state_path.exists():
        try:
            previous = str(json.loads(state_path.read_text(encoding="utf-8")).get("event_id") or "")
        except (OSError, ValueError, TypeError):
            previous = ""
    if previous == str(row["event_id"]):
        return 0
    state_path.write_text(
        json.dumps({"event_id": str(row["event_id"]), "user_id": user_id}, ensure_ascii=False),
        encoding="utf-8",
    )
    run_id = os.getenv("CGM_AGENT_RUN_ID", "normal")
    if os.getenv("CGM_ACCEPTANCE_MODE") == "1":
        print(
            f"[CGM模拟验收] run_id={run_id} 模拟日期={row['ts_start']} "
            f"检测到新的血糖事件（{row['event_type']}），请先查看数据再决定是否需要回应。"
        )
    else:
        print(f"检测到新的血糖事件（{row['event_type']}），请先查看数据再决定是否需要回应。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
