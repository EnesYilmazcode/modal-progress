"""Folds progress events into one snapshot that the renderers draw."""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field

# A finished bar stays on the display this long.
STALE_AFTER = 15.0
# An open bar counts as running until its container has been silent this long,
# so a slow step between updates doesn't read as a dead container.
SILENT_AFTER = 60.0


@dataclass
class Bar:
    id: str
    task: str
    desc: str = ""
    n: float = 0
    total: float | None = None
    done: bool = False
    seen: float = 0.0

    @property
    def fraction(self) -> float | None:
        if not self.total:
            return None
        return max(0.0, min(self.n / self.total, 1.0))

    def label(self) -> str:
        count = f"{self.n:g}/{self.total:g}" if self.total else f"{self.n:g}"
        return f"{self.desc or self.task[-6:]} {count}"


@dataclass
class State:
    name: str = "map"
    total: int | None = None
    done: int = 0
    failed: int = 0
    started: float = field(default_factory=time.monotonic)
    finished: float | None = None
    bars: dict[str, Bar] = field(default_factory=dict)  # shown bar per container
    first_seen: dict[str, float] = field(default_factory=dict)
    born: dict[str, float] = field(default_factory=dict)  # bar id -> first event
    last_error: str | None = None
    # Inputs Modal counts as finished. It counts an output when it arrives,
    # before the result is downloaded, so this runs ahead of `done` when
    # results are large.
    remote: int | None = None
    remote_all: float | None = None  # when remote reached total
    closed: set = field(default_factory=set)  # inputs that have closed a bar
    phases: dict[str, tuple[str, float]] = field(default_factory=dict)  # task -> (label, when set)

    def apply(self, ev: dict, now: float | None = None) -> None:
        """Takes one event sent by bar() inside a container."""
        now = time.monotonic() if now is None else now
        task = ev.get("task") or "local"
        self.first_seen.setdefault(task, now)
        if "phase" in ev:
            self.phases[task] = (ev["phase"], now)
            return
        if ev.get("done") and ev.get("input"):
            self.closed.add(ev["input"])
        born = self.born.setdefault(ev["id"], now)
        bar = self.bars.get(task)
        if bar is None or bar.id != ev["id"]:
            # A container can have several open bars (nested loops, concurrent
            # inputs). Show the newest open one, so an outer bar's update
            # doesn't reset the inner bar on screen.
            if bar is not None and not bar.done and born < self.born[bar.id]:
                return
            bar = self.bars[task] = Bar(id=ev["id"], task=task)
        bar.desc = ev.get("desc") or ""
        bar.n = ev.get("n", 0)
        bar.total = ev.get("total")
        bar.done = bool(ev.get("done"))
        bar.seen = now

    def result(self, ok: bool, error: str | None = None) -> None:
        """Counts one finished input from the map's result stream."""
        self.done += 1
        if not ok:
            self.failed += 1
            self.last_error = error

    def remote_count(self, completed: int, now: float | None = None) -> None:
        """Takes Modal's own count of finished inputs."""
        now = time.monotonic() if now is None else now
        if completed <= (self.remote or 0):
            return
        self.remote = completed
        if self.total and completed >= self.total and self.remote_all is None:
            self.remote_all = now

    def active(self, now: float | None = None) -> list[Bar]:
        """Bars from containers that are still sending, oldest container first."""
        now = time.monotonic() if now is None else now
        live = [b for b in self.bars.values() if now - b.seen < (STALE_AFTER if b.done else SILENT_AFTER)]
        return sorted(live, key=lambda b: self.first_seen[b.task])

    def running(self, now: float | None = None) -> list[Bar]:
        return [b for b in self.active(now) if not b.done]

    def busy(self, now: float | None = None) -> int:
        return len(self.running(now))

    def receiving(self) -> bool:
        """Every input has finished on Modal, but results are still coming back."""
        return bool(self.total and self.remote and self.remote >= self.total and self.done < self.total)

    def uploading(self) -> bool:
        """Every input has closed its bar, but Modal hasn't counted them all as
        finished: the functions are returning, often uploading a large result."""
        if not self.total or self.done >= self.total or self.receiving():
            return False
        return len(self.closed) >= self.total and not self.running()

    def waiting(self) -> bool:
        """Nothing has come back yet: the image is building or containers are starting."""
        return not self.first_seen and not self.done and not self.remote

    def elapsed(self, now: float | None = None) -> float:
        end = self.finished if self.finished is not None else (time.monotonic() if now is None else now)
        return max(0.0, end - self.started)

    def effective(self, now: float | None = None) -> float:
        """Finished inputs plus the finished share of the ones still running."""
        partial = sum(b.fraction or 0.0 for b in self.running(now))
        value = max(self.done, self.remote or 0, len(self.closed)) + partial
        return min(value, self.total) if self.total else value

    def eta(self, now: float | None = None) -> float | None:
        if not self.total or self.finished is not None or self.receiving() or self.done >= self.total:
            return None
        now = time.monotonic() if now is None else now
        progress = self.effective(now)
        # Too early to say until 5% of the work is done.
        if progress <= 0 or progress < self.total * 0.05 or progress >= self.total:
            return None
        # Time from the first container event, so the cold start doesn't count as work.
        began = min(self.first_seen.values(), default=self.started)
        working = now - began
        if working <= 0:
            return None
        return max(0.0, (self.total - progress) * working / progress)

    def startup(self) -> float | None:
        """Seconds from the start until the first container reported."""
        if not self.first_seen:
            return None
        return min(self.first_seen.values()) - self.started

    def phase_summary(self, now: float | None = None) -> str | None:
        """Each stage the containers are in, with how many are in it: "rendering frames x3, uploading"."""
        now = time.monotonic() if now is None else now
        counts = Counter(label for label, when in self.phases.values() if now - when < SILENT_AFTER)
        if not counts:
            return None
        return ", ".join(f"{label} x{n}" if n > 1 else label for label, n in counts.most_common())

    def signature(self, now: float | None = None) -> tuple:
        """What a new log line must differ in to be worth printing."""
        return (
            self.done,
            self.failed,
            self.remote,
            self.busy(now),
            self.waiting(),
            self.uploading(),
            self.receiving(),
            self.phase_summary(now),
        )


def clock(seconds: float | None) -> str:
    """0:07, 3:41, 1:02:09."""
    if seconds is None:
        return "?"
    s = int(round(seconds))
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
