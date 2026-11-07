"""A map whose containers crash on startup, like a helper module that never got mounted.

    modal run repro/crash_loop.py > crash.log 2>&1
"""

import modal

app = modal.App("modal-progress-crash-loop")

if not modal.is_local():
    import helper_that_was_not_mounted  # noqa: F401


@app.function()
def work(i: int) -> int:
    return i


@app.local_entrypoint()
def main():
    print(list(work.map(range(4), return_exceptions=True)))
