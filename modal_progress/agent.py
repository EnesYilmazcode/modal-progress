"""Plain map progress for `modal run` when nobody is at a terminal.

Modal tracks map progress with its own counter and draws it as a rich live
bar. When stdout is a file or a pipe, which is how a background agent runs
it, rich draws nothing until the end, so the log has no progress at all.
install() wraps that counter so it also prints a throttled plain line.
No change to the user's code is needed.
"""

from __future__ import annotations

import sys
import time
from typing import Callable, TextIO

from .render import PREFIX
from .state import clock


class LineThrottle:
    """Turns (completed, total) updates into at most one line per interval."""

    def __init__(self, tag: str, interval: float = 5.0, out: TextIO | None = None, now=time.monotonic):
        self.tag = tag
        self.interval = interval
        self.out = out
        self.now = now
        self.started = now()
        self.last = self.started
        self.last_line: tuple[int, int] | None = None

    def __call__(self, completed: int, total: int) -> None:
        t = self.now()
        finished = total > 0 and completed >= total
        if (completed, total) == self.last_line:
            return
        if not finished and t - self.last < self.interval:
            return
        self.last = t
        self.last_line = (completed, total)
        elapsed = t - self.started
        if finished:
            line = f"{PREFIX} {self.tag} {completed}/{total} done, {clock(elapsed)}"
        else:
            pct = f" ({100 * completed / total:.0f}%)" if total else ""
            rate = completed / elapsed if elapsed > 0 else 0
            # One finished input says little about the rest, so wait for 5%.
            trusted = total and completed >= max(2, total * 0.05)
            eta = f", eta {clock((total - completed) / rate)}" if rate > 0 and trusted else ""
            line = f"{PREFIX} {self.tag} {completed}/{total} done{pct}, {clock(elapsed)} elapsed{eta}"
        out = self.out or sys.stdout
        out.write(line + "\n")
        out.flush()


def install(interval: float = 5.0, manager_cls=None) -> Callable[[], None]:
    """Patches Modal's rich output manager. Returns a function that undoes it.

    The hook is a private Modal API, so if it moves this does nothing and
    `modal run` behaves as usual.
    """
    if manager_cls is None:
        try:
            from modal._output.rich import RichOutputManager as manager_cls
        except ImportError:
            return lambda: None

    original = getattr(manager_cls, "function_progress_callback", None)
    if original is None or getattr(original, "_modal_progress", False):
        return lambda: None

    def function_progress_callback(self, tag: str, total: int | None):
        update = original(self, tag, total)
        if getattr(self, "is_terminal", True):
            return update
        line = LineThrottle(tag, interval)

        def both(completed: int, total: int) -> None:
            update(completed, total)
            try:
                line(completed, total)
            except Exception:
                pass

        return both

    function_progress_callback._modal_progress = True
    manager_cls.function_progress_callback = function_progress_callback

    def undo() -> None:
        manager_cls.function_progress_callback = original

    return undo
