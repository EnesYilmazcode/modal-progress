"""Two ways to draw a State: plain lines for logs and agents, rich bars for people."""

from __future__ import annotations

import sys
import threading
import time
from typing import TextIO

from .state import State, clock

PREFIX = "[progress]"


# Plain lines name at most this many running containers.
SHOW_BARS = 4


def plain_line(state: State, now: float | None = None) -> str:
    name = state.name
    if state.finished is not None:
        counts = f"{state.done}/{state.total}" if state.total else f"{state.done}"
        line = f"{PREFIX} {name} finished: {counts} done, {state.failed} failed, {clock(state.elapsed())}"
        return line + _timings(state)
    parts = []
    if state.total:
        pct = 100 * state.done / state.total
        parts.append(f"{name} {state.done}/{state.total} done ({pct:.0f}%)")
    else:
        parts.append(f"{name} {state.done} done")
    parts.append(f"{state.failed} failed")
    running = state.running(now)
    if state.waiting():
        parts.append("waiting for containers to start")
    elif state.receiving():
        parts.append(f"all {state.total} finished on Modal, receiving results")
    elif state.uploading():
        parts.append(f"all {state.total} inputs closed their bars, waiting for Modal to collect results")
    else:
        if state.remote and state.remote > state.done:
            parts.append(f"{state.remote} finished on Modal")
        if state.bars:
            parts.append(f"{len(running)} running")
    summary = state.phase_summary(now)
    if summary:
        parts.append(f"phase: {summary}")
    parts.append(f"{clock(state.elapsed(now))} elapsed")
    eta = state.eta(now)
    if eta is not None:
        parts.append(f"eta {clock(eta)}")
    line = f"{PREFIX} " + ", ".join(parts)
    if running and not state.receiving():
        shown = ", ".join(b.label() for b in running[:SHOW_BARS])
        more = len(running) - SHOW_BARS
        line += f" | {shown}" + (f", +{more} more" if more > 0 else "")
    return line


def _timings(state: State) -> str:
    """Where the time went, when a phase took 5 s or more: a slow start or a slow download."""
    parts = []
    startup = state.startup()
    if startup is not None and startup >= 5:
        parts.append(f"startup {clock(startup)}")
    if state.remote_all is not None and state.finished is not None:
        receiving = state.finished - state.remote_all
        if receiving >= 5:
            parts.append(f"receiving results {clock(receiving)}")
    return f" ({', '.join(parts)})" if parts else ""


class PlainRenderer:
    """Prints a line when the counts change, a heartbeat while they don't,
    and failures as they happen.

    Changes are printed at most once every `interval` seconds, and the
    heartbeat comes every `heartbeat` seconds, so a quiet job costs a few
    lines a minute instead of one every few seconds. Lines have a fixed
    shape so an agent reading the log can grep them.
    """

    def __init__(
        self,
        state: State,
        lock: threading.Lock,
        out: TextIO | None = None,
        interval: float = 5.0,
        heartbeat: float = 30.0,
    ):
        self.state = state
        self.lock = lock
        self.out = out or sys.stdout
        self.interval = interval
        self.heartbeat = max(heartbeat, interval)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_key: tuple | None = None
        self._last_at: float | None = None
        self._changed_at: float | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="modal-progress-plain", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(min(1.0, self.interval)):
            self.tick()

    def _write(self, line: str) -> None:
        self.out.write(line + "\n")
        self.out.flush()

    def tick(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self.lock:
            key = self.state.signature(now)
            since = None if self._last_at is None else now - self._last_at
            changed = key != self._last_key
            if changed or self._changed_at is None:
                self._changed_at = now
            if since is not None and not (changed and since >= self.interval) and since < self.heartbeat:
                return
            if self.state.total and self.state.done >= self.state.total:
                return  # close() prints the final line
            line = plain_line(self.state, now)
            if not changed:
                line += f" (no change for {clock(now - self._changed_at)})"
            self._last_key, self._last_at = key, now
        self._write(line)

    def failure(self, error: str) -> None:
        self._write(f"{PREFIX} {self.state.name}: an input failed: {error}")

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        with self.lock:
            line = plain_line(self.state)
        self._write(line)


def rich_view(state: State, now: float | None = None):
    """One overall bar, then one row per container that is still reporting."""
    from rich.progress_bar import ProgressBar
    from rich.table import Table
    from rich.text import Text

    grid = Table.grid(padding=(0, 1))
    grid.add_column(no_wrap=True)
    grid.add_column(no_wrap=True, min_width=14)
    grid.add_column(width=32)
    grid.add_column(justify="right", no_wrap=True)
    grid.add_column(no_wrap=True)

    total = state.total or max(state.done, 1)
    eta = state.eta(now)
    tail = Text(clock(state.elapsed(now)), style="cyan")
    if eta is not None:
        tail.append(f"  eta {clock(eta)}", style="dim")
    if state.failed:
        tail.append(f"  {state.failed} failed", style="red")
    grid.add_row(
        Text(state.name, style="bold"),
        Text(f"{state.busy(now)} running", style="dim"),
        ProgressBar(total=total, completed=state.effective(now), width=32, complete_style="green"),
        Text(f"{state.done}/{state.total if state.total else '?'}"),
        tail,
    )
    for b in state.active(now):
        short = b.task[-6:]
        bar = ProgressBar(
            total=b.total or 1,
            completed=b.n if b.total else 0,
            pulse=not b.total and not b.done,
            width=32,
            complete_style="grey50" if b.done else "magenta",
        )
        count = f"{b.n:g}/{b.total:g}" if b.total else f"{b.n:g}"
        grid.add_row(
            Text(f"  {short}", style="dim"),
            Text(b.desc or "", style="grey50" if b.done else ""),
            bar,
            Text(count, style="dim"),
            Text("idle" if b.done else "", style="dim"),
        )
    return grid


class RichRenderer:
    """Live bars in a terminal.

    Under `modal run`, Modal already has a live display on screen, and two
    live displays fight over the cursor. So the view joins Modal's own
    display when there is one, and opens its own otherwise.
    """

    def __init__(self, state: State, lock: threading.Lock, console=None):
        self.state = state
        self.lock = lock
        self.console = console
        self._live = None
        self._group = None

    def __rich__(self):
        with self.lock:
            return rich_view(self.state)

    def start(self) -> None:
        self._group = _modal_render_group()
        if self._group is not None:
            self._group.renderables.append(self)
            return
        from rich.live import Live

        self._live = Live(self, console=self.console, refresh_per_second=4, transient=False)
        self._live.start()

    def failure(self, error: str) -> None:
        pass  # the failed count is on the overall row

    def close(self) -> None:
        if self._live is not None:
            self._live.stop()
            return
        if self._group is not None and self in self._group.renderables:
            self._group.renderables.remove(self)
        from rich.console import Console

        with self.lock:
            line = plain_line(self.state)
        (self.console or Console()).print(line, highlight=False)


def _modal_render_group():
    """Modal's live render group, if a `modal run` display is up. Private API, so guarded."""
    try:
        from modal._output.manager import OutputManager

        return getattr(OutputManager.get(), "_current_render_group", None)
    except Exception:
        return None
