<h1 align="center">modal-progress</h1>

<p align="center"><b>Live progress from inside Modal containers.</b><br>
Progress bars per container in a terminal, plain progress lines in a log file.</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/EnesYilmazcode/modal-progress/main/assets/media/with-and-without.gif" width="820" alt="The same 60-input Modal job logged to a file: without modal-progress the log is silent until the end, with it there are progress lines throughout">
</p>

`modal run` draws `.map` progress as a live terminal bar. When the output goes to a file or a pipe, which is how coding agents run commands, nothing is printed until the job ends, so there is no way to tell a slow job from a stuck one. modal-progress prints a plain line when the progress changes, a heartbeat when it doesn't, and lets each container report its own progress.

## Install

```bash
pip install modal-progress
```

Requires Python 3.10+ and modal 1.x.

## Usage

### No code changes

Use `modal-progress run` in place of `modal run`. Arguments are passed through unchanged.

```bash
modal-progress run app.py > log.txt
```

```
[progress] work 0/60 done (0%), 0:05 elapsed
[progress] work 12/60 done (20%), 0:15 elapsed, eta 0:34
[progress] work 30/60 done (50%), 0:25 elapsed, eta 0:18
[progress] work 51/60 done (85%), 0:40 elapsed, eta 0:06
[progress] work 60/60 done, 0:46
```

In a terminal it leaves `modal run` output as is.

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
[progress] work 0/60 done (0%), 0 failed, waiting for containers to start, 0:01 elapsed
[progress] work 21/60 done (35%), 0 failed, 5 running, 0:26 elapsed, eta 0:36 | input 23 20/40, input 20 36/40, input 25 18/40, input 24 16/40, +1 more
[progress] work: an input failed: ValueError: bad input 37
[progress] work 52/60 done (87%), 1 failed, 3 running, 0:52 elapsed, eta 0:07 | input 52 12/40, input 53 15/40, input 54 8/40
[progress] work finished: 60/60 done, 1 failed, 1:02
```

A line is printed when the counts change, at most once every 5 seconds, and a heartbeat every 30 seconds when they don't, so a long quiet stretch costs two lines a minute. A heartbeat ends with `(no change for M:SS)`, the time since the counts last moved, so a job that is slow reads differently from one that has been stuck on the same batch for minutes. Each line names up to four running containers and how far along they are. Failures are reported the moment they happen.

When results are large, Modal counts an input as finished before its result has downloaded. The log says so instead of sitting at the old count, and the last line says where the time went:

```
[progress] render 1/4 done (25%), 0 failed, 3 finished on Modal, 1 running, 0:39 elapsed, eta 0:01 | chunk 3 135/150
[progress] render 2/4 done (50%), 0 failed, all 4 finished on Modal, receiving results, 0:44 elapsed
[progress] render finished: 4/4 done, 0 failed, 1:00 (receiving results 0:16)
```

The other phases are `waiting for containers to start` (image build and cold start, also reported as `startup` on the last line) and `all N inputs closed their bars, waiting for Modal to collect results` (the functions are returning, often uploading a large result). Every line starts with `[progress]`, so it can be filtered with `grep`. A full example is in [`examples/fanout.py`](https://github.com/EnesYilmazcode/modal-progress/blob/main/examples/fanout.py).

## API

| Call | Where | What it does |
|---|---|---|
| `bar(iterable=None, total=None, desc="", every=0.5)` | container | tqdm-style wrapper. Also supports `update(n)`, `set_description(s)`, `close()`, and `with`. Sends at most one update every `every` seconds. |
| `report(n, total=None, desc="")` | container | Sets progress for the current input directly, for loops `bar()` can't wrap. |
| `phase(label)` | container | Names the stage the container is in, like `phase("loading model")`. The log shows the latest label, and a new label counts as a change. |
| `watch(app, name="map", mode=None, interval=None, record=None, heartbeat=None)` | caller | Context manager that collects and draws progress. `record` writes every event to a JSON lines file. |
| `w.track(results, total=None)` | caller | Passes map results through and counts them. Exceptions count as failures. |

## Configuration

| Variable | Values | Default |
|---|---|---|
| `MODAL_PROGRESS` | `rich`, `plain`, `off` | `rich` in a terminal, `plain` otherwise. `off` also stops containers from sending. |
| `MODAL_PROGRESS_INTERVAL` | minimum seconds between plain lines | `5` |
| `MODAL_PROGRESS_HEARTBEAT` | seconds between lines when nothing changes | `30` |

## How it works

Each container sends its latest progress to a [Modal Queue](https://modal.com/docs/reference/modal.Queue) named `modal-progress`, in a partition keyed by the app ID, batched every 0.25 seconds from a background thread. `watch()` reads that partition and combines it with the map's results as they come back, and with Modal's own count of finished inputs when there is one. A `bar()` update costs about 0.2 µs, so it can wrap tight loops.

`modal-progress run` needs no queue. It hooks Modal's own count of finished inputs and prints it as plain lines when stdout isn't a terminal. Inside a `watch()`, that count goes to the watcher instead, so the log doesn't get two sets of lines.

## Limitations

- Uses three private Modal internals: `App._get_container_app()` to find the app ID in a container, Modal's live display to add the bars to it, and its map progress callback for `modal-progress run`. All are guarded, so if one changes you lose progress output, not the job. Tested on modal 1.6.1.
- Works with `modal run` and `app.run()`. A deployed function called from a different app reports to its own app's partition, so the caller won't see it.
- One `watch()` per app at a time. Two watchers on the same app read the same partition and split the events between them.
- Without a `watch()`, events from `bar()` go nowhere. Once the partition fills, new ones are dropped, so the job never waits on progress.
- A container shows one row: its newest open bar. With nested loops that is the inner one.
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
