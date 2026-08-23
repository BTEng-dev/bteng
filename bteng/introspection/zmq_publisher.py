"""ZMQ PUB publisher that streams Inspector events to external monitoring tools.

Topic: ``b"bteng"``  Message: UTF-8 JSON

Optional dependency — install with ``pip install bteng[zmq]``.
"""
from __future__ import annotations

import json
import queue
import threading
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from bteng.introspection.inspector import Inspector, NodeExecutionRecord

_QUEUE_MAXSIZE = 1_000
_TOPIC = b"bteng "   # trailing space separates topic from payload per ZMQ convention
_STOP = object()


class ZmqPublisher:
    """Drain Inspector events through a background thread and publish via ZMQ PUB.

    Usage::

        pub = ZmqPublisher(port=5555)
        pub.attach(inspector)
        pub.start()
        # ... run tree ...
        pub.stop()
    """

    def __init__(self, port: int = 1667, host: str = "*") -> None:
        try:
            import zmq as _zmq  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "pyzmq is required for ZmqPublisher. "
                "Install it with: pip install bteng[zmq]"
            ) from exc

        self._port = port
        self._host = host
        self._queue: queue.Queue["NodeExecutionRecord"] = queue.Queue(
            maxsize=_QUEUE_MAXSIZE
        )
        self._inspector: Optional["Inspector"] = None
        self._sub_id: Optional[int] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # ── Public API ────────────────────────────────────────────────────────────

    def attach(self, inspector: "Inspector") -> "ZmqPublisher":
        """Subscribe to inspector events. Returns self for chaining."""
        if self._inspector is not None:
            raise RuntimeError("Already attached to an inspector. Call stop() first.")
        self._inspector = inspector
        self._sub_id = inspector.subscribe(self._on_record)
        return self

    def start(self) -> "ZmqPublisher":
        """Start the background publisher thread."""
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="ZmqPublisher", daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        """Unsubscribe from inspector, drain queue, and close socket."""
        if self._inspector is not None and self._sub_id is not None:
            self._inspector.unsubscribe(self._sub_id)
            self._inspector = None
            self._sub_id = None

        self._stop_event.set()
        self._queue.put(_STOP)
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

    # ── Inspector callback (called on tick thread) ────────────────────────────

    def _on_record(self, record: "NodeExecutionRecord") -> None:
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            # Drop oldest, enqueue latest — real-time display, no backpressure
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(record)
            except queue.Full:
                pass

    # ── Background thread ─────────────────────────────────────────────────────

    def _run(self) -> None:
        import zmq

        ctx = zmq.Context.instance()
        sock = ctx.socket(zmq.PUB)
        sock.bind(f"tcp://{self._host}:{self._port}")

        try:
            while not self._stop_event.is_set():
                record = self._queue.get()
                if record is _STOP:
                    break

                payload = json.dumps({
                    "ts":     record.tick_time,
                    "uid":    record.uid,
                    "name":   record.name,
                    "type":   record.node_type.value,
                    "status": record.status.value,
                    "dur_ms": round(record.duration * 1000.0, 3),
                    "reason": record.feedback_message,
                }).encode()

                try:
                    sock.send(_TOPIC + payload, flags=zmq.NOBLOCK)
                except zmq.Again:
                    pass
        finally:
            sock.close(linger=0)
