"""Clock protocol — allows swapping wall clock for ROS 2 sim time."""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def monotonic(self) -> float: ...


class WallClock:
    """Default clock backed by time.monotonic()."""

    def monotonic(self) -> float:
        return time.monotonic()
