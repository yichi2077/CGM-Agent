from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import sleep as default_sleep
from typing import Callable


@dataclass
class SimClock:
    """Simulation clock that can replay CGM time faster than wall time."""

    start: datetime
    acceleration: float = 300.0
    max_speed: bool = False
    sleep_fn: Callable[[float], None] = default_sleep
    _current: datetime = field(init=False)

    def __post_init__(self) -> None:
        if self.acceleration <= 0:
            raise ValueError("acceleration must be positive")
        self._current = _aware_utc(self.start)

    def now(self) -> datetime:
        return self._current

    def advance_to(self, target: datetime) -> datetime:
        target = _aware_utc(target)
        if target < self._current:
            raise ValueError("simulation time cannot move backwards")
        delta_seconds = (target - self._current).total_seconds()
        if delta_seconds > 0 and not self.max_speed:
            self.sleep_fn(delta_seconds / self.acceleration)
        self._current = target
        return self._current


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0)
