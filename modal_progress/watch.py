"""Caller side: collect progress from the containers and draw it."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from typing import Iterable, Iterator, TextIO

from . import container
from .render import PlainRenderer, RichRenderer
from .state import State


def pick_mode(mode: str | None = None, out: TextIO | None = None) -> str:
    """rich in a terminal, plain lines anywhere else. MODAL_PROGRESS overrides."""
    mode = (mode or os.environ.get("MODAL_PROGRESS") or "").lower()
    if mode in ("rich", "plain", "off"):
        return mode
    stream = out or sys.stdout
    return "rich" if getattr(stream, "isatty", lambda: False)() else "plain"


class Watcher:
    def __init__(
        self,
        app=None,
        name: str = "map",
        mode: str | None = None,
        interval: float | None = None,
        out: TextIO | None = None,
        record: str | None = None,
    ):
        self.app = app
        self.state = State(name=name)
        self.lock = threading.Lock()
        self.mode = pick_mode(mode, out)
        interval = interval or float(os.environ.get("MODAL_PROGRESS_INTERVAL", 5))
        if self.mode == "rich":
            self.renderer = RichRenderer(self.state, self.lock)
        elif self.mode == "plain":
            self.renderer = PlainRenderer(self.state, self.lock, out=out, interval=interval)
        else:
            self.renderer = None
        self._stop = threading.Event()
        self._drain: threading.Thread | None = None
        self._queue = None
        self._key: str | None = None
        self._record_path = record
        self._record: TextIO | None = None

    def __enter__(self) -> "Watcher":
        self.state.started = time.monotonic()
        if self._record_path:
            self._record = open(self._record_path, "w", encoding="utf8")
        container.set_local_sink(self.feed)
        key = getattr(self.app, "app_id", None) if self.app is not None else None
        if key:
            import modal

            self._key = key
            self._queue = modal.Queue.from_name(container.QUEUE_NAME, create_if_missing=True)
            self._drain = threading.Thread(target=self._drain_loop, name="modal-progress-drain", daemon=True)
            self._drain.start()
        if self.renderer:
            self.renderer.start()
        return self

    def __exit__(self, *exc) -> None:
        container.set_local_sink(None)
        if self._drain is not None:
            self._stop.set()
            self._drain.join(timeout=3)
            try:
                # Containers flush before returning, so the last events can still be queued.
                for ev in self._queue.get_many(1024, block=False, partition=self._key):
                    self.feed(ev)
                self._queue.clear(partition=self._key)
            except Exception:
                pass
        with self.lock:
            self.state.finished = time.monotonic()
        if self.renderer:
            self.renderer.close()
        if self._record:
            self._write({"end": True, "done": self.state.done, "failed": self.state.failed})
            self._record.close()

    def _write(self, row: dict) -> None:
        """One JSON line per event, timed from the start. Call with the lock held."""
        if self._record:
            row["at"] = round(time.monotonic() - self.state.started, 3)
            row["wall"] = round(time.time(), 3)  # container clocks can differ from ours by seconds
            self._record.write(json.dumps(row) + "\n")

    def feed(self, ev: dict) -> None:
        with self.lock:
            self.state.apply(ev)
            self._write({"ev": ev})

    def _drain_loop(self) -> None:
        while not self._stop.is_set():
            try:
                batch = self._queue.get_many(256, timeout=1.0, partition=self._key)
            except Exception:
                time.sleep(1.0)
                continue
            for ev in batch:
                self.feed(ev)

    def track(self, results: Iterable, total: int | None = None, name: str | None = None) -> Iterator:
        """Passes results through and counts them. Exceptions count as failures.

            for r in w.track(work.map(xs, return_exceptions=True), total=len(xs)):
                ...
        """
        if total is None:
            try:
                total = len(results)  # type: ignore[arg-type]
            except TypeError:
                pass
        with self.lock:
            self.state.total = total
            if name:
                self.state.name = name
            self._write({"start": True, "name": self.state.name, "total": total})
        it = iter(results)
        while True:
            try:
                r = next(it)
            except StopIteration:
                return
            except Exception as e:
                self._result(False, _describe(e))
                raise
            if isinstance(r, BaseException):
                self._result(False, _describe(r))
            else:
                self._result(True)
            yield r

    def _result(self, ok: bool, error: str | None = None) -> None:
        with self.lock:
            self.state.result(ok, error)
            self._write({"result": ok, "error": error})
        if not ok and self.renderer:
            self.renderer.failure(error or "")


def _describe(e: BaseException) -> str:
    text = str(e).strip().splitlines()
    return f"{type(e).__name__}: {text[-1]}" if text else type(e).__name__


def watch(
    app=None,
    name: str = "map",
    mode: str | None = None,
    interval: float | None = None,
    out=None,
    record: str | None = None,
) -> Watcher:
    """Shows live progress for a map while the block runs.

        with watch(app, name="work") as w:
            results = list(w.track(work.map(xs, return_exceptions=True)))

    `record` writes every event to a JSON lines file, for replaying the run later.
    """
    return Watcher(app=app, name=name, mode=mode, interval=interval, out=out, record=record)
