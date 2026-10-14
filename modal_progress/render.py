"""Two ways to draw a State: plain lines for logs and agents, rich bars for people."""

from __future__ import annotations

import sys
import threading
from typing import TextIO

from .state import State, clock

PREFIX = "[progress]"


def plain_line(state: State, now: float | None = None) -> str:
    name = state.name
    if state.finished is not None:
        counts = f"{state.done}/{state.total}" if state.total else f"{state.done}"
        return f"{PREFIX} {name} finished: {counts} done, {state.failed} failed, {clock(state.elapsed())}"
    parts = []
    if state.total:
        pct = 100 * state.done / state.total
        parts.append(f"{name} {state.done}/{state.total} done ({pct:.0f}%)")
    else:
        parts.append(f"{name} {state.done} done")
    parts.append(f"{state.failed} failed")
    if state.bars:
        parts.append(f"{state.busy(now)} running")
    parts.append(f"{clock(state.elapsed(now))} elapsed")
    if state.total:
        parts.append(f"eta {clock(state.eta(now))}")
    return f"{PREFIX} " + ", ".join(parts)


class PlainRenderer:
    """Prints one line every `interval` seconds, plus failures as they happen.

    Lines have a fixed shape so an agent reading the log can grep them.
    """

    def __init__(self, state: State, lock: threading.Lock, out: TextIO | None = None, interval: float = 5.0):
        self.state = state
        self.lock = lock
        self.out = out or sys.stdout
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="modal-progress-plain", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self.tick()

    def _write(self, line: str) -> None:
        self.out.write(line + "\n")
        self.out.flush()

    def tick(self, now: float | None = None) -> None:
        with self.lock:
            line = plain_line(self.state, now)
        self._write(line)

    def failure(self, error: str) -> None:
        self._write(f"{PREFIX} {self.state.name}: an input failed: {error}")

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        self.tick()
