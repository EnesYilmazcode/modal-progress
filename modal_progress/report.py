"""Container side: ship progress events to whoever is watching the map.

Events go to one named Modal Queue, in a partition keyed by the app ID, so
the caller can find them without any change to the function's signature.
A background thread batches them, keeping only the latest event per bar,
so a tight loop never waits on the network.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Callable

QUEUE_NAME = "modal-progress"
PARTITION_TTL = 3600
FLUSH_EVERY = 0.25

# Set by watch() for in-process runs (the simulator and tests).
_local_sink: Callable[[dict], None] | None = None
_task_override = threading.local()


def set_local_sink(sink: Callable[[dict], None] | None) -> None:
    global _local_sink
    _local_sink = sink


def disabled() -> bool:
    return os.environ.get("MODAL_PROGRESS", "").lower() == "off"


def task_id() -> str:
    override = getattr(_task_override, "task", None)
    if override:
        return override
    return os.environ.get("MODAL_TASK_ID") or "local"


def input_id() -> str | None:
    try:
        import modal

        return modal.current_input_id()
    except Exception:
        return None


def _remote_target():
    """The queue and partition key to send to, or (None, None) outside Modal."""
    if os.environ.get("MODAL_IS_REMOTE") != "1":
        return None, None
    import modal

    key = os.environ.get("MODAL_PROGRESS_KEY")
    if not key:
        app = modal.App._get_container_app()
        key = app.app_id if app is not None else None
    if not key:
        return None, None
    return modal.Queue.from_name(QUEUE_NAME, create_if_missing=True), key


class _Sender:
    def __init__(self) -> None:
        self._pending: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._sending = False
        self._thread: threading.Thread | None = None
        self._target = None

    def send(self, ev: dict) -> None:
        with self._lock:
            self._pending[ev["id"]] = ev
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="modal-progress", daemon=True)
                self._thread.start()

    def flush(self, timeout: float = 2.0) -> None:
        """Waits until everything sent so far has left the process."""
        self._wake.set()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if not self._pending and not self._sending:
                    return
            time.sleep(0.01)

    def _run(self) -> None:
        while True:
            self._wake.wait(FLUSH_EVERY)
            self._wake.clear()
            with self._lock:
                batch = list(self._pending.values())
                self._pending.clear()
                self._sending = bool(batch)
            if batch:
                self._put(batch)
                with self._lock:
                    self._sending = False

    def _put(self, batch: list[dict]) -> None:
        # Progress must never break the job, so every failure here is swallowed.
        try:
            if _local_sink is not None:
                for ev in batch:
                    _local_sink(ev)
                return
            if self._target is None:
                self._target = _remote_target()
            queue, key = self._target
            if queue is not None:
                queue.put_many(batch, partition=key, partition_ttl=PARTITION_TTL)
        except Exception:
            pass


_sender = _Sender()
