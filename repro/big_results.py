# 4 chunks that report progress and return 80 MB each, like a render that
# ships frames back. Modal counts each chunk as finished before its result
# has downloaded, so the log should show "finished on Modal" ahead of done.
#
#   modal run repro/big_results.py > log.txt
import os
import time

import modal

from modal_progress import report, watch

app = modal.App("modal-progress-big-results")
image = modal.Image.debian_slim().add_local_python_source("modal_progress")


@app.function(image=image, cpu=1)
def chunk(i: int) -> bytes:
    frames = 60 + 30 * i
    for f in range(frames):
        time.sleep(0.25)
        report(f + 1, frames, desc=f"chunk {i}")
    return os.urandom(80 * 1024 * 1024)


@app.local_entrypoint()
def main():
    with watch(app, name="render") as w:
        sizes = [len(b) for b in w.track(chunk.map(range(4), order_outputs=False), total=4)]
    print(f"{len(sizes)} chunks, {sum(sizes) // 2**20} MB")
