import io
import threading

from modal_progress.render import PlainRenderer, plain_line, rich_view
from modal_progress.state import State


def test_plain_line_shape():
    s = State(name="work", started=0, total=60)
    for _ in range(24):
        s.result(True)
    s.result(False, "boom")
    s.apply({"id": "a", "task": "t1", "n": 5, "total": 10}, now=20)
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
