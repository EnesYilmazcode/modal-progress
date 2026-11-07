"""Plain lines for what happens before and around the work: waiting for a
worker, containers starting, and containers that keep crashing on startup.

Modal draws these as a spinner and a queue bar. Without a terminal neither
is drawn, so a job waiting 3 minutes for a GPU looks exactly like a hung
one, and a crash loop shows only as tracebacks buried in the log.
"""

from __future__ import annotations

import re
import sys
import threading
import time
from collections import Counter
from typing import Callable, TextIO

from .render import PREFIX
from .state import clock

# Modal's server sends this as a log line once a function keeps failing to start.
CRASH_LOOP = re.compile(r"Function (\S+) is crash-looping")
ERROR_LINE = re.compile(r"^(\w+(?:\.\w+)*(?:Error|Exception|Exit)\b.*)$")

# Container states by the name Modal gives them, minus the TASK_STATE_ prefix.
_RUNNING = {"ACTIVE", "IDLE"}
_SKIP = {"UNSPECIFIED", "COMPLETED", "PREEMPTIBLE"}


def state_name(state: int) -> str:
    try:
        from modal_proto import api_pb2

        return api_pb2.TaskState.Name(state).removeprefix("TASK_STATE_")
    except Exception:
        return str(state)


class Signals:
    """Turns Modal's startup updates into plain lines.

    queue() and task_state() take what Modal's output manager receives,
    log() takes each log line, and poll() prints a heartbeat while a
    function waits for a worker. on_crash_loop is called once, with the
    line that was printed.
    """

    def __init__(
        self,
        out: TextIO | None = None,
        interval: float = 5.0,
        heartbeat: float = 30.0,
        now=time.monotonic,
        on_crash_loop: Callable[[str], None] | None = None,
    ):
        self.out = out
        self.interval = interval
        self.heartbeat = max(heartbeat, interval)
        self.now = now
        self.on_crash_loop = on_crash_loop
        self.queued: dict[str, tuple[float, str, float]] = {}  # function -> (since, description, last line)
        self.states: dict[str, str] = {}
        self._containers_line: str | None = None
        self._containers_at: float | None = None
        self._containers_due = False
        self.last_error: str | None = None
        self.crash_loop: str | None = None
        self.app_page_url: str | None = None
        self._lock = threading.Lock()

    def _write(self, line: str) -> None:
        out = self.out or sys.stdout
        out.write(f"{PREFIX} {line}\n")
        out.flush()

    def queue(self, function_id: str, completed: int, total: int | None, description: str | None) -> None:
        t = self.now()
        with self._lock:
            if total and completed >= total:
                entry = self.queued.pop(function_id, None)
                if entry is not None:
                    self._write(f"got a worker after {clock(t - entry[0])}: {entry[1]}")
                return
            if function_id in self.queued:
                since, old, last = self.queued[function_id]
                if description and description != old:
                    self.queued[function_id] = (since, description, t)
                    self._write(f"waiting for a worker: {description}, {clock(t - since)} in queue")
                return
            description = description or function_id
            self.queued[function_id] = (t, description, t)
            self._write(f"waiting for a worker: {description}")

    def task_state(self, task_id: str, state: int) -> None:
        with self._lock:
            self.states[task_id] = state_name(state)
            self._containers_due = True
            self._containers(self.now())

    def _containers(self, t: float) -> None:
        """One line of container counts, on change, at most once per interval."""
        if not self._containers_due:
            return
        if self._containers_at is not None and t - self._containers_at < self.interval:
            return
        counts = Counter(s for s in self.states.values() if s not in _SKIP)
        self._containers_due = False
        if not counts:
            return
        running = sum(n for s, n in counts.items() if s in _RUNNING)
        parts = [f"{running} running"] if running else []
        parts += [f"{n} {s.lower().replace('_', ' ')}" for s, n in counts.most_common() if s not in _RUNNING]
        line = "containers: " + ", ".join(parts)
        if line != self._containers_line:
            self._containers_line, self._containers_at = line, t
            self._write(line)

    def log(self, data: str) -> None:
        for raw in data.splitlines():
            text = raw.strip()
            error = ERROR_LINE.match(text)
            if error:
                self.last_error = error.group(1)[:300]
            match = CRASH_LOOP.search(text)
            if match and self.crash_loop is None:
                line = f"crash loop: {match.group(1)} keeps failing to start"
                if self.last_error:
                    line += f", last error: {self.last_error}"
                with self._lock:
                    self.crash_loop = line
                    self._write(line)
                if self.on_crash_loop is not None:
                    self.on_crash_loop(line)

    def poll(self) -> None:
        t = self.now()
        with self._lock:
            self._containers(t)
            for fid, (since, description, last) in list(self.queued.items()):
                if t - last >= self.heartbeat:
                    self.queued[fid] = (since, description, t)
                    self._write(f"waiting for a worker: {description}, {clock(t - since)} in queue")

    def start(self) -> None:
        def run() -> None:
            while True:
                time.sleep(1.0)
                try:
                    self.poll()
                except Exception:
                    return

        threading.Thread(target=run, name="modal-progress-signals", daemon=True).start()


def hook(manager_cls, signals: Signals) -> Callable[[], None]:
    """Feeds one manager class's startup updates and logs to `signals`,
    when it has no terminal. Private Modal API, so every piece is guarded
    and a missing one is skipped. Returns a function that undoes it."""
    undos = []

    def wrap(name: str, make) -> None:
        original = getattr(manager_cls, name, None)
        if original is None or getattr(original, "_modal_progress", False):
            return
        wrapped = make(original)
        wrapped._modal_progress = True
        setattr(manager_cls, name, wrapped)
        undos.append(lambda: setattr(manager_cls, name, original))

    def quiet(self) -> bool:
        return not getattr(self, "is_terminal", True)

    def make_queue(original):
        def update_queueing_progress(self, *, function_id, completed, total, description):
            original(self, function_id=function_id, completed=completed, total=total, description=description)
            if quiet(self):
                try:
                    signals.queue(function_id, completed, total, description)
                except Exception:
                    pass

        return update_queueing_progress

    def make_state(original):
        def update_task_state(self, task_id, state):
            original(self, task_id, state)
            if quiet(self):
                try:
                    signals.task_state(task_id, state)
                except Exception:
                    pass

        return update_task_state

    def make_log(original):
        async def put_streaming_log(self, log, prefix=""):
            try:
                signals.log(getattr(log, "data", "") or "")
            except Exception:
                pass
            return await original(self, log, prefix)

        return put_streaming_log

    def make_url(original):
        def update_app_page_url(self, app_page_url):
            original(self, app_page_url)
            signals.app_page_url = app_page_url

        return update_app_page_url

    wrap("update_queueing_progress", make_queue)
    wrap("update_task_state", make_state)
    wrap("put_streaming_log", make_log)
    wrap("update_app_page_url", make_url)
    return lambda: [u() for u in undos]


def app_id(url: str | None) -> str | None:
    match = re.search(r"\bap-[A-Za-z0-9]+", url or "")
    return match.group(0) if match else None
