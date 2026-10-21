"""Time source. Services take a `Clock` so expiry rules can be tested with a simulated clock."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FakeClock:
    """A clock that only moves when told to. For tests."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime.now(UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta
