from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def _read_json_object(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8-sig") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("tool-call input must be a JSON object")
    return payload


def _parse_iso_datetime(raw: str) -> datetime:
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid ISO 8601 datetime: {raw}") from exc


def _period_to_window_label(period: str) -> str:
    return {
        "daily": "day",
        "weekly": "week",
        "monthly": "month",
    }.get(period, period)
