# 60 inputs, each takes 4 s, at most 6 containers at once: about 40 s of map.
import time
import modal

app = modal.App("progress-repro")


@app.function(max_containers=6)
def work(i: int) -> int:
    time.sleep(4)
    if i == 37:
        raise ValueError("bad input 37")
    return i * i


@app.local_entrypoint()
def main():
    t = time.time()
    results = list(work.map(range(60), return_exceptions=True))
    failed = [i for i, r in enumerate(results) if isinstance(r, Exception)]
    print(f"done: {len(results)} results, failed={failed}, {time.time() - t:.0f}s")
