from __future__ import annotations

from datetime import datetime


def _parse_iso_datetime(raw: str) -> datetime:
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid ISO 8601 datetime: {raw}") from exc


def _period_to_window_label(period: str) -> str:
    return {"daily": "day", "weekly": "week", "monthly": "month"}.get(period, period)
