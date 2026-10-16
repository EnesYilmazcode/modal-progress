"""Folds progress events into one snapshot that the renderers draw."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

# A container that has sent nothing for this long drops off the display.
STALE_AFTER = 15.0


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

    def apply(self, ev: dict, now: float | None = None) -> None:
        """Takes one event sent by bar() inside a container."""
        now = time.monotonic() if now is None else now
        task = ev.get("task") or "local"
        self.first_seen.setdefault(task, now)
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

    def active(self, now: float | None = None) -> list[Bar]:
        """Bars from containers that are still sending, oldest container first."""
        now = time.monotonic() if now is None else now
        live = [b for b in self.bars.values() if now - b.seen < STALE_AFTER]
        return sorted(live, key=lambda b: self.first_seen[b.task])

    def busy(self, now: float | None = None) -> int:
        return sum(1 for b in self.active(now) if not b.done)

    def elapsed(self, now: float | None = None) -> float:
        end = self.finished if self.finished is not None else (time.monotonic() if now is None else now)
        return max(0.0, end - self.started)

    def effective(self, now: float | None = None) -> float:
        """Finished inputs plus the finished share of the ones still running."""
        partial = sum(b.fraction or 0.0 for b in self.active(now) if not b.done)
        value = self.done + partial
        return min(value, self.total) if self.total else value

    def eta(self, now: float | None = None) -> float | None:
        # Too early to say until 5% of inputs are back.
        if not self.total or self.finished is not None or self.done < max(1, self.total * 0.05):
            return None
        now = time.monotonic() if now is None else now
        progress = self.effective(now)
        # Time from the first container event, so the cold start doesn't count as work.
        began = min(self.first_seen.values(), default=self.started)
        working = now - began
        if progress <= 0 or working <= 0:
            return None
        return max(0.0, (self.total - progress) * working / progress)


def clock(seconds: float | None) -> str:
    """0:07, 3:41, 1:02:09."""
    if seconds is None:
        return "?"
    s = int(round(seconds))
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
