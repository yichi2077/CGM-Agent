from __future__ import annotations

from typing import Any


def require_bool(value: Any, field: str) -> bool:
    # Strict JSON boundary: Python truthiness would turn "false" into True.
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def optional_bool(value: Any, field: str, *, default: bool) -> bool:
    if value is None:
        return default
    return require_bool(value, field)


def parse_limit(value: Any) -> int | None:
    if value is None:
        return None
    return require_int(value, "limit", minimum=1, maximum=10000)


def optional_int(
    value: Any,
    field: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    if value is None:
        return default
    return require_int(value, field, minimum=minimum, maximum=maximum)


def require_int(value: Any, field: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return value


def require_enum(value: Any, field: str, allowed: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{field} must be one of: {', '.join(allowed)}")
    return value
