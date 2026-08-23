"""Cooperative cancellation token for async nodes."""
from __future__ import annotations

import threading


class CancellationToken:
    """Shared cooperative cancellation signal.

    Passed to executeAsync() / execute_async(); background thread polls
    is_cancelled() to support cooperative shutdown.

    Also exposes is_set() for backward compatibility with threading.Event usage.
    """

    @classmethod
    def create(cls) -> "CancellationToken":
        return cls()

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """Signal the background thread to stop. Idempotent."""
        self._event.set()

    def reset(self) -> None:
        """Clear the cancellation flag (e.g., before reusing the token)."""
        self._event.clear()

    def is_cancelled(self) -> bool:
        """Poll from the background thread inside the work loop."""
        return self._event.is_set()

    # backward compat: allows using token like threading.Event
    def is_set(self) -> bool:
        return self._event.is_set()

    def __repr__(self) -> str:
        return f"CancellationToken(cancelled={self.is_cancelled()})"
