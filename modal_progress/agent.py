"""Plain map progress for `modal run` when nobody is at a terminal.

Modal tracks map progress with its own counter and draws it as a rich live
bar. When stdout is a file or a pipe, which is how a background agent runs
it, rich draws nothing until the end, so the log has no progress at all.
install() wraps that counter so it also prints a throttled plain line.
No change to the user's code is needed.

The same hook feeds an open watch(), which can then tell inputs that
finished on Modal from results that have arrived.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Callable, TextIO

from .render import PREFIX
from .state import clock

Update = Callable[[int, int], None]

# Callables that may claim a map's counter: (tag) -> update or None.
_listeners: list[Callable[[str], Update | None]] = []
# Set by install(): print lines for maps no watcher claims.
_lines: dict | None = None


class LineThrottle:
    """Turns (completed, total) updates into plain lines.

    A change prints at once if the last line is `interval` seconds old.
    Otherwise it waits, and poll() prints it when the interval is up, so
    no count is skipped for good. poll() also prints a heartbeat after
    `heartbeat` seconds without a change.
    """

    def __init__(
        self,
        tag: str,
        interval: float = 5.0,
        out: TextIO | None = None,
        now=time.monotonic,
        heartbeat: float = 30.0,
    ):
        self.tag = tag
        self.interval = interval
        self.heartbeat = max(heartbeat, interval)
        self.out = out
        self.now = now
        self.started = now()
        self.last = self.started
        self.last_line: tuple[int, int] | None = None
        self.latest: tuple[int, int] = (0, 0)
        self.first_done: float | None = None
        self.finished = False
        self._lock = threading.Lock()

    def __call__(self, completed: int, total: int) -> None:
        t = self.now()
        with self._lock:
            if completed and self.first_done is None:
                self.first_done = t
            self.latest = (completed, total)
            finished = total > 0 and completed >= total
            if self.latest == self.last_line or self.finished:
                return
            if not finished and t - self.last < self.interval:
                return
            self._print(t)

    def poll(self) -> None:
        """Prints a held-back change or a heartbeat when one is due."""
        t = self.now()
        with self._lock:
            if self.finished:
                return
            due = self.latest != self.last_line and t - self.last >= self.interval
            if due or t - self.last >= self.heartbeat:
                self._print(t)

    def _print(self, t: float) -> None:
        completed, total = self.latest
        self.last = t
        self.last_line = self.latest
        elapsed = t - self.started
        if total > 0 and completed >= total:
            self.finished = True
            line = f"{PREFIX} {self.tag} {completed}/{total} done, {clock(elapsed)}"
        else:
            pct = f" ({100 * completed / total:.0f}%)" if total else ""
            line = f"{PREFIX} {self.tag} {completed}/{total} done{pct}, {clock(elapsed)} elapsed{self._eta(t)}"
        out = self.out or sys.stdout
        out.write(line + "\n")
        out.flush()

    def _eta(self, t: float) -> str:
        completed, total = self.latest
        # One finished input says little about the rest, so wait for 5%.
        if not total or completed < max(2, total * 0.05) or self.first_done is None:
            return ""
        # Rate from the first finished input, so the cold start doesn't count.
        span = t - self.first_done
        if span <= 0:
            return ""
        return f", eta {clock((total - completed) * span / (completed - 1))}"

    def start(self) -> None:
        """Polls from a background thread until the map finishes."""

        def run() -> None:
            while not self.finished:
                time.sleep(min(1.0, self.interval or 1.0))
                try:
                    self.poll()
                except Exception:
                    return

        threading.Thread(target=run, name="modal-progress-lines", daemon=True).start()


def _manager_classes() -> list:
    classes = []
    try:
        from modal._output.rich import RichOutputManager

        classes.append(RichOutputManager)
    except ImportError:
        pass
    try:
        from modal._output.manager import DisabledOutputManager

        classes.append(DisabledOutputManager)
    except ImportError:
        pass
    return classes


def hook(manager_cls) -> Callable[[], None]:
    """Wraps one manager class's progress callback. Returns a function that undoes it."""
    original = getattr(manager_cls, "function_progress_callback", None)
    if original is None or getattr(original, "_modal_progress", False):
        return lambda: None

    def function_progress_callback(self, tag: str, total: int | None):
        update = original(self, tag, total)
        extra = None
        try:
            for listener in list(_listeners):
                extra = listener(tag)
                if extra is not None:
                    break
            if extra is None and _lines is not None and not getattr(self, "is_terminal", True):
                extra = LineThrottle(tag, _lines["interval"], heartbeat=_lines["heartbeat"])
                if _lines["poll"]:
                    extra.start()
        except Exception:
            extra = None
        if extra is None:
            return update

        def both(completed: int, total: int) -> None:
            update(completed, total)
            try:
                extra(completed, total)
            except Exception:
                pass

        return both

    function_progress_callback._modal_progress = True
    manager_cls.function_progress_callback = function_progress_callback

    def undo() -> None:
        manager_cls.function_progress_callback = original

    return undo


def hook_all() -> Callable[[], None]:
    undos = [hook(cls) for cls in _manager_classes()]
    return lambda: [u() for u in undos]


def listen(listener: Callable[[str], Update | None]) -> Callable[[], None]:
    _listeners.append(listener)
    return lambda: _listeners.remove(listener) if listener in _listeners else None


def install(interval: float = 5.0, manager_cls=None, heartbeat: float = 30.0, poll: bool = True) -> Callable[[], None]:
    """Patches Modal's rich output manager. Returns a function that undoes it.

    The hook is a private Modal API, so if it moves this does nothing and
    `modal run` behaves as usual.
    """
    global _lines
    if manager_cls is None:
        try:
            from modal._output.rich import RichOutputManager as manager_cls
        except ImportError:
            return lambda: None
    if getattr(manager_cls, "function_progress_callback", None) is None:
        return lambda: None
    if _lines is not None:
        return lambda: None
    undo_hook = hook(manager_cls)
    _lines = {"interval": interval, "heartbeat": heartbeat, "poll": poll}

    def undo() -> None:
        global _lines
        _lines = None
        undo_hook()

    return undo
