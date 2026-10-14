# 60 inputs over 6 containers. Each input walks 40 steps, input 37 fails
# two thirds of the way in. Run from the repo root:
#
#   modal run examples/fanout.py                    rich bars in a terminal
#   modal run examples/fanout.py > log.txt          plain lines, like an agent sees
#   modal run examples/fanout.py --record run.jsonl  also save every event
import random
import time

import modal

from modal_progress import bar, watch

app = modal.App("modal-progress-example")
image = modal.Image.debian_slim().add_local_python_source("modal_progress")


@app.function(image=image, max_containers=6)
def work(i: int) -> int:
    speed = random.Random(i).uniform(0.6, 1.6)
    for step in bar(range(40), desc=f"input {i}"):
        time.sleep(0.1 * speed)
        if i == 37 and step == 26:
            raise ValueError("bad input 37")
    return i * i


@app.local_entrypoint()
def main(record: str = ""):
    xs = range(60)
    with watch(app, name="work", record=record or None) as w:
        results = list(w.track(work.map(xs, return_exceptions=True, order_outputs=False), total=len(xs)))
    failed = sum(isinstance(r, Exception) for r in results)
    print(f"{len(results)} results, {failed} failed")
