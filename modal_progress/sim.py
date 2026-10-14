"""A fake fan-out that runs on threads, so the display can be tried without Modal.

Each thread plays one container: it pulls inputs, walks a bar() for each,
and raises on the inputs listed in `fail`. Results come back in completion
order, like `.map(..., order_outputs=False, return_exceptions=True)`.
"""

from __future__ import annotations

import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterator

from . import container
from .container import bar


def simulate(
    inputs: int = 60,
    containers: int = 6,
    steps: int = 40,
    step_time: float = 0.1,
    fail: tuple[int, ...] = (37,),
    seed: int = 0,
) -> Iterator:
    rng = random.Random(seed)
    # Some inputs are slower than others, as in a real map.
    speed = [rng.uniform(0.6, 1.6) for _ in range(inputs)]
    names = iter(f"sim-{k:02d}-{rng.getrandbits(24):06x}" for k in range(containers))
    names_lock = threading.Lock()

    def boot():
        with names_lock:
            container._task_override.task = next(names)

    def work(i: int) -> int:
        for step in bar(range(steps), desc=f"input {i}", every=0.2):
            time.sleep(step_time * speed[i])
            if i in fail and step == steps * 2 // 3:
                raise ValueError(f"bad input {i}")
        return i * i

    with ThreadPoolExecutor(max_workers=containers, initializer=boot) as pool:
        futures = [pool.submit(work, i) for i in range(inputs)]
        for f in as_completed(futures):
            e = f.exception()
            yield e if e is not None else f.result()
