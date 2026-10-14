# What does a bar() cost inside a real container? Times the same loop with
# and without it, in the same container, several rounds each.
#
#   modal run scripts/overhead.py
import statistics
import time

import modal

app = modal.App("modal-progress-overhead")
image = modal.Image.debian_slim().add_local_python_source("modal_progress")

N = 200_000


@app.function(image=image)
def measure(rounds: int = 5) -> dict:
    from modal_progress import bar

    def plain():
        t = time.perf_counter()
        for _ in range(N):
            pass
        return time.perf_counter() - t

    def wrapped():
        t = time.perf_counter()
        for _ in bar(range(N), desc="overhead"):
            pass
        return time.perf_counter() - t

    plain(), wrapped()  # warm up, and open the queue once
    a = [plain() for _ in range(rounds)]
    b = [wrapped() for _ in range(rounds)]
    per_step = (statistics.median(b) - statistics.median(a)) / N
    return {"plain_s": statistics.median(a), "bar_s": statistics.median(b), "ns_per_step": per_step * 1e9}


@app.local_entrypoint()
def main():
    r = measure.remote()
    print(f"{N} steps: plain {r['plain_s']:.4f} s, with bar {r['bar_s']:.4f} s")
    print(f"bar() adds {r['ns_per_step']:.0f} ns per step")
    for step_ms in (1, 10, 100):
        print(f"  on a {step_ms} ms step that is {r['ns_per_step'] / (step_ms * 1e6) * 100:.3f}%")
