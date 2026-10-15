<h1 align="center">modal-progress</h1>

<p align="center"><b>Live progress from inside Modal containers.</b><br>
Progress bars per container in a terminal, plain progress lines in a log file.</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/EnesYilmazcode/modal-progress/main/assets/media/with-and-without.gif" width="820" alt="The same 60-input Modal job logged to a file: without modal-progress the log is silent until the end, with it there is a progress line every 5 seconds">
</p>

`modal run` draws `.map` progress as a live terminal bar. When the output goes to a file or a pipe, which is how coding agents run commands, nothing is printed until the job ends, so there is no way to tell a slow job from a stuck one. modal-progress prints a plain line every few seconds instead, and lets each container report its own progress.

## Install

```bash
pip install git+https://github.com/EnesYilmazcode/modal-progress
```

Requires Python 3.10+ and modal 1.x.

## Usage

### No code changes

Use `modal-progress run` in place of `modal run`. Arguments are passed through unchanged.

```bash
modal-progress run app.py > log.txt
```

```
[progress] work 11/60 done (18%), 0:13 elapsed, eta 1:00
[progress] work 19/60 done (32%), 0:20 elapsed, eta 0:44
[progress] work 29/60 done (48%), 0:25 elapsed, eta 0:27
[progress] work 60/60 done, 0:47
```

In a terminal it behaves exactly like `modal run`.

### Progress from inside each container

Wrap a loop in `bar()` inside the function, and wrap the map in `watch()` on the caller's side.

```python
import modal
from modal_progress import bar, watch

app = modal.App("example")
image = modal.Image.debian_slim().add_local_python_source("modal_progress")

@app.function(image=image)
def work(i: int) -> int:
    for step in bar(range(40), desc=f"input {i}"):
        ...  # one step of work
    return i

@app.local_entrypoint()
def main():
    xs = range(60)
    with watch(app, name="work") as w:
        results = list(w.track(work.map(xs, return_exceptions=True), total=len(xs)))
```

In a terminal, this shows an overall bar plus one bar per container. In a log file, it prints:

```
[progress] work 20/60 done (33%), 0 failed, 6 running, 0:25 elapsed, eta 0:31
[progress] work: an input failed: ValueError: bad input 37
[progress] work 40/60 done (67%), 1 failed, 6 running, 0:40 elapsed, eta 0:14
[progress] work finished: 60/60 done, 1 failed, 0:57
```

Failures are reported the moment they happen. Every line starts with `[progress]`, so it can be filtered with `grep`. A full example is in [`examples/fanout.py`](https://github.com/EnesYilmazcode/modal-progress/blob/main/examples/fanout.py).

## API

| Call | Where | What it does |
|---|---|---|
| `bar(iterable=None, total=None, desc="", every=0.5)` | container | tqdm-style wrapper. Also supports `update(n)`, `set_description(s)`, `close()`, and `with`. Sends at most one update every `every` seconds. |
| `report(n, total=None, desc="")` | container | Sets progress for the current input directly, for loops `bar()` can't wrap. |
| `watch(app, name="map", mode=None, interval=None, record=None)` | caller | Context manager that collects and draws progress. `record` writes every event to a JSON lines file. |
| `w.track(results, total=None)` | caller | Passes map results through and counts them. Exceptions count as failures. |

## Configuration

| Variable | Values | Default |
|---|---|---|
| `MODAL_PROGRESS` | `rich`, `plain`, `off` | `rich` in a terminal, `plain` otherwise. `off` also stops containers from sending. |
| `MODAL_PROGRESS_INTERVAL` | seconds between plain lines | `5` |

## How it works

Each container sends its latest progress to a [Modal Queue](https://modal.com/docs/reference/modal.Queue) named `modal-progress`, in a partition keyed by the app ID, batched every 0.25 seconds from a background thread. `watch()` reads that partition and combines it with the map's results as they come back. A `bar()` update costs about 0.2 µs, so it can wrap tight loops.

`modal-progress run` needs no queue. It hooks Modal's own count of finished inputs and prints it as plain lines when stdout isn't a terminal.

## Limitations

- Uses three private Modal internals: `App._get_container_app()` to find the app ID in a container, Modal's live display to add the bars to it, and its map progress callback for `modal-progress run`. All are guarded, so if one changes you lose progress output, not the job. Tested on modal 1.6.1.
- Works with `modal run` and `app.run()`. A deployed function called from a different app reports to its own app's partition, so the caller won't see it.
- Progress shows up within about a second of happening.

## Development

```bash
pip install -e ".[dev]"
python -m pytest -q
python -m modal_progress demo        # simulated map, no Modal account needed
modal run examples/fanout.py         # real map on Modal
```

## License

MIT
