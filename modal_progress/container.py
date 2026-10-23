"""Container side: ship progress events to whoever is watching the map.

Events go to one named Modal Queue, in a partition keyed by the app ID, so
the caller can find them without any change to the function's signature.
A background thread batches them, keeping only the latest event per bar,
so a tight loop never waits on the network.
"""

from __future__ import annotations

import os
import sys
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
    # Inside a container modal is already loaded. Elsewhere, importing it
    # just to learn there is no input would cost a second.
    modal = sys.modules.get("modal")
    if modal is None:
        return None
    try:
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
        self._sent = threading.Condition(self._lock)
        self._queued = 0  # events handed to send()
        self._done = 0  # events that have left the process
        self._thread: threading.Thread | None = None
        self._target = None

    def send(self, ev: dict) -> None:
        with self._lock:
            self._pending[ev["id"]] = ev
            self._queued += 1
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="modal-progress", daemon=True)
                self._thread.start()

    def flush(self, timeout: float = 2.0) -> None:
        """Waits until the events sent before this call have left the process.

        Only those: other threads may keep sending, and waiting for an empty
        buffer would stall every input while any container is busy.
        """
        with self._lock:
            target = self._queued
        self._wake.set()
        with self._sent:
            self._sent.wait_for(lambda: self._done >= target, timeout=timeout)

    def _run(self) -> None:
        while True:
            self._wake.wait(FLUSH_EVERY)
            self._wake.clear()
            with self._lock:
                batch = list(self._pending.values())
                self._pending.clear()
                upto = self._queued
            if batch:
                self._put(batch)
            with self._sent:
                self._done = upto
                self._sent.notify_all()

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
                # A full partition (nobody watching) must drop events, not block.
                queue.put_many(batch, block=False, partition=key, partition_ttl=PARTITION_TTL)
        except Exception:
            pass


_sender = _Sender()


class bar:
    """A tqdm-style bar that reports to the caller instead of a terminal.

        for step in bar(range(100), desc=f"input {i}"):
            ...

    Updates are sent at most once every `every` seconds, plus the first and
    the last, so wrapping a fast loop costs almost nothing.
    """

    def __init__(self, iterable=None, total: float | None = None, desc: str = "", every: float = 0.5):
        if total is None and iterable is not None:
            try:
                total = len(iterable)
            except TypeError:
                total = None
        self.iterable = iterable
        self.total = total
        self.desc = desc
        self.every = every
        self.n: float = 0
        self.closed = False
        self.id = os.urandom(4).hex()
        self._last = 0.0
        self._off = disabled()  # read once: os.environ costs a microsecond per lookup
        self._emit(force=True)

    def __iter__(self):
        # The hot path, so the throttle check is inlined.
        clock = time.monotonic
        try:
            for item in self.iterable:
                yield item
                self.n += 1
                if not self._off and clock() - self._last >= self.every:
                    self._emit(force=True)
        finally:
            self.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def update(self, k: float = 1) -> None:
        self.n += k
        self._emit()

    def set_description(self, desc: str) -> None:
        self.desc = desc
        self._emit()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self._emit(force=True)
        if not self._off:
            _sender.flush()

    def _emit(self, force: bool = False) -> None:
        if self._off:
            return
        now = time.monotonic()
        if not force and now - self._last < self.every:
            return
        self._last = now
        _sender.send(
            {
                "id": self.id,
                "task": task_id(),
                "input": input_id(),
                "desc": self.desc,
                "n": self.n,
                "total": self.total,
                "done": self.closed,
                "t": time.time(),
            }
        )


_manual: dict[str | None, bar] = {}


def report(n: float, total: float | None = None, desc: str = "") -> None:
    """Sets progress for the current input directly, for loops bar() can't wrap.

    The bar closes itself once n reaches total.
    """
    key = input_id()
    b = _manual.get(key)
    fresh = b is None or b.closed
    if fresh:
        b = _manual[key] = bar(total=total, desc=desc)
    if total is not None:
        b.total = total
    if desc:
        b.desc = desc
    b.n = n
    if b.total and n >= b.total:
        b.close()
        _manual.pop(key, None)
    else:
        b._emit(force=fresh)


def phase(label: str) -> None:
    """Names the stage the current container is in, e.g. phase("loading model")."""
    if disabled():
        return
    _sender.send(
        {"id": f"phase:{task_id()}", "task": task_id(), "input": input_id(), "phase": label, "t": time.time()}
    )
