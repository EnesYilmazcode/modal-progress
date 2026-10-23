import io
import threading

from modal_progress.render import PlainRenderer, plain_line, rich_view
from modal_progress.state import State


def test_plain_line_shape():
    s = State(name="work", started=0, total=60)
    for _ in range(24):
        s.result(True)
    s.result(False, "boom")
    s.apply({"id": "a", "task": "t1", "n": 5, "total": 10}, now=5)
    s.bars["t1"].seen = 20
    line = plain_line(s, now=20)
    assert line.startswith("[progress] work 25/60 done (42%), 1 failed, 1 running, 0:20 elapsed, eta ")


def test_plain_line_without_total():
    s = State(name="work", started=0)
    s.result(True)
    assert plain_line(s, now=3) == "[progress] work 1 done, 0 failed, 0:03 elapsed"


def test_finished_line():
    s = State(name="work", started=0, total=2, finished=45)
    s.result(True)
    s.result(False)
    assert plain_line(s) == "[progress] work finished: 2/2 done, 1 failed, 0:45"


def test_renderer_prints_failures_and_final_line():
    out = io.StringIO()
    s = State(name="work", started=0, total=1)
    r = PlainRenderer(s, threading.Lock(), out=out, interval=999)
    r.failure("ValueError: bad input 37")
    s.result(False)
    s.finished = s.started + 1
    r.close()
    lines = out.getvalue().splitlines()
    assert lines == [
        "[progress] work: an input failed: ValueError: bad input 37",
        "[progress] work finished: 1/1 done, 1 failed, 0:01",
    ]


def test_rich_view_has_a_row_per_container():
    from rich.console import Console

    s = State(name="work", started=0, total=60)
    for k in range(3):
        s.apply({"id": str(k), "task": f"ta-{k}", "desc": f"input {k}", "n": k, "total": 40}, now=1)
    console = Console(width=100, file=io.StringIO(), force_terminal=False)
    console.print(rich_view(s, now=2))
    text = console.file.getvalue()
    assert "work" in text
    assert text.count("input ") == 3


def test_plain_line_names_running_containers():
    s = State(name="render", started=0, total=8)
    for k in range(6):
        s.apply({"id": str(k), "task": f"t{k}", "desc": f"chunk {k}", "n": 30 * k, "total": 150}, now=1)
    line = plain_line(s, now=2)
    assert line.endswith("| chunk 0 0/150, chunk 1 30/150, chunk 2 60/150, chunk 3 90/150, +2 more")


def test_plain_line_while_waiting_and_receiving():
    s = State(name="render", started=0, total=4)
    assert plain_line(s, now=30) == "[progress] render 0/4 done (0%), 0 failed, waiting for containers to start, 0:30 elapsed"
    s.apply({"id": "a", "task": "t1", "n": 150, "total": 150, "done": True}, now=245)
    s.remote_count(4, now=412)
    assert plain_line(s, now=420) == (
        "[progress] render 0/4 done (0%), 0 failed, all 4 finished on Modal, receiving results, 7:00 elapsed"
    )


def test_finished_line_says_where_the_time_went():
    s = State(name="render", started=0, total=4)
    s.apply({"id": "a", "task": "t1", "n": 0, "total": 150}, now=245)
    s.remote_count(4, now=412)
    for _ in range(4):
        s.result(True)
    s.finished = 523
    assert plain_line(s) == (
        "[progress] render finished: 4/4 done, 0 failed, 8:43 (startup 4:05, receiving results 1:51)"
    )


def test_renderer_prints_changes_and_heartbeats_only():
    out = io.StringIO()
    s = State(name="work", started=0, total=4)
    r = PlainRenderer(s, threading.Lock(), out=out, interval=5, heartbeat=30)
    for t in range(1, 121):  # two minutes of 1 s ticks
        if t == 40:
            s.apply({"id": "a", "task": "t1", "n": 1, "total": 10}, now=t)
        if t == 41:
            s.result(True)  # within 5 s of the last line, so held back
        if 41 < t < 120:
            s.bars["t1"].seen = t
        r.tick(now=t)
    lines = out.getvalue().splitlines()
    stamps = [l.split(" elapsed")[0].rsplit(", ", 1)[1] for l in lines]
    # A heartbeat while waiting, the change at 0:40, the result held back
    # from 0:41 to 0:45, then heartbeats.
    assert stamps == ["0:01", "0:31", "0:40", "0:45", "1:15", "1:45"]
    assert "waiting for containers" in lines[1]
    assert lines[3].startswith("[progress] work 1/4 done")
    # Heartbeats say how long the counts have been unchanged; changes don't.
    assert lines[1].endswith("(no change for 0:30)")
    assert "no change for" not in lines[2]
    assert lines[4].endswith("(no change for 0:30)")
    assert lines[5].endswith("(no change for 1:00)")


def test_phase_is_shown_and_a_new_phase_prints():
    out = io.StringIO()
    s = State(name="work", started=0, total=4)
    r = PlainRenderer(s, threading.Lock(), out=out, interval=5, heartbeat=30)
    s.apply({"id": "phase:t1", "task": "t1", "phase": "loading model"}, now=1)
    r.tick(now=1)
    s.apply({"id": "phase:t1", "task": "t1", "phase": "rendering frames"}, now=40)
    r.tick(now=40)
    lines = out.getvalue().splitlines()
    assert lines[0].endswith("phase: loading model, 0:01 elapsed")
    assert "phase: rendering frames" in lines[1]


def test_phase_counts_containers_in_each_stage():
    s = State(name="work", started=0, total=4)
    s.apply({"id": "phase:a", "task": "a", "phase": "rendering frames"}, now=1)
    s.apply({"id": "phase:b", "task": "b", "phase": "rendering frames"}, now=2)
    s.apply({"id": "phase:c", "task": "c", "phase": "uploading"}, now=3)
    assert s.phase_summary(now=4) == "rendering frames x2, uploading"
