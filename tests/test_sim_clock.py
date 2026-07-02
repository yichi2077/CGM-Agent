from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from hermes_cgm_agent.services.simulation import SimClock


class SimClockTests(unittest.TestCase):
    def test_advance_uses_acceleration(self) -> None:
        slept: list[float] = []
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        clock = SimClock(start=start, acceleration=300, sleep_fn=slept.append)

        now = clock.advance_to(start + timedelta(minutes=5))

        self.assertEqual(now, start + timedelta(minutes=5))
        self.assertEqual(slept, [1.0])

    def test_max_speed_does_not_sleep(self) -> None:
        slept: list[float] = []
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        clock = SimClock(start=start, max_speed=True, sleep_fn=slept.append)

        clock.advance_to(start + timedelta(hours=1))

        self.assertEqual(slept, [])

    def test_rejects_backwards_time(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        clock = SimClock(start=start)

        with self.assertRaises(ValueError):
            clock.advance_to(start - timedelta(seconds=1))


if __name__ == "__main__":
    unittest.main()
