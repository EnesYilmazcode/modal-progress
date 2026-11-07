import asyncio
import io
import sys
import threading
import types

from modal_progress import signals
from modal_progress.signals import Signals, app_id


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def lines(out):
    return out.getvalue().splitlines()


def test_queue_wait_prints_start_heartbeats_and_the_wait():
    out, clock = io.StringIO(), Clock()
    s = Signals(out=out, heartbeat=30, now=clock)
    s.queue("fu-1", 0, None, "Waiting for L4 GPU")
    for t in range(1, 133):
        clock.t = t
        s.poll()
    s.queue("fu-1", 1, 1, None)
    assert lines(out) == [
        "[progress] waiting for a worker: Waiting for L4 GPU",
        "[progress] waiting for a worker: Waiting for L4 GPU, 0:30 in queue",
        "[progress] waiting for a worker: Waiting for L4 GPU, 1:00 in queue",
        "[progress] waiting for a worker: Waiting for L4 GPU, 1:30 in queue",
        "[progress] waiting for a worker: Waiting for L4 GPU, 2:00 in queue",
        "[progress] got a worker after 2:12: Waiting for L4 GPU",
    ]


def test_a_finished_queue_nobody_saw_start_prints_nothing():
    out = io.StringIO()
    Signals(out=out).queue("fu-1", 1, 1, None)
    assert out.getvalue() == ""


def test_container_states_print_on_change_and_skip_finished_ones():
    out, clock = io.StringIO(), Clock()
    s = Signals(out=out, interval=5, now=clock)
    s.task_state("ta-1", 2)  # worker assigned
    clock.t = 1
    s.task_state("ta-2", 3)  # loading image, held back
    s.task_state("ta-1", 3)
    clock.t = 6
    s.poll()
    clock.t = 20
    s.task_state("ta-1", 8)  # idle counts as running
    s.task_state("ta-2", 5)  # completed is left out, printed once the interval is up
    clock.t = 25
    s.poll()
    assert lines(out) == [
        "[progress] containers: 1 worker assigned",
        "[progress] containers: 2 loading image",
        "[progress] containers: 1 running, 1 loading image",
        "[progress] containers: 1 running",
    ]


def test_crash_loop_names_the_function_and_the_last_error():
    out, seen = io.StringIO(), []
    s = Signals(out=out, on_crash_loop=seen.append)
    s.log("Traceback (most recent call last):\n  File \"/root/app.py\", line 3\n")
    s.log("ModuleNotFoundError: No module named 'sibling_helper'\n")
    s.log("Function crash_app.work is crash-looping: containers are repeatedly failing to start.\n")
    s.log("Function crash_app.work is crash-looping: containers are repeatedly failing to start.\n")
    want = (
        "[progress] crash loop: crash_app.work keeps failing to start, "
        "last error: ModuleNotFoundError: No module named 'sibling_helper'"
    )
    assert lines(out) == [want]
    assert seen == [want.removeprefix("[progress] ")]


def test_app_id_from_the_page_url():
    assert app_id("https://modal.com/apps/me/main/ap-AbC123xyz") == "ap-AbC123xyz"
    assert app_id(None) is None


class FakeManager:
    def __init__(self, terminal):
        self.is_terminal = terminal
        self.calls = []

    def update_queueing_progress(self, *, function_id, completed, total, description):
        self.calls.append("queue")

    def update_task_state(self, task_id, state):
        self.calls.append("state")

    async def put_streaming_log(self, log, prefix=""):
        self.calls.append("log")

    def update_app_page_url(self, url):
        self.calls.append("url")


def test_hook_feeds_signals_only_without_a_terminal_and_undoes():
    out = io.StringIO()
    s = Signals(out=out)
    undo = signals.hook(FakeManager, s)
    try:
        for terminal in (True, False):
            m = FakeManager(terminal)
            m.update_queueing_progress(function_id="fu-1", completed=0, total=None, description=None)
            m.update_task_state("ta-1", 3)
            m.update_app_page_url("https://modal.com/apps/x/main/ap-1")
            asyncio.run(m.put_streaming_log(types.SimpleNamespace(data="hi\n")))
            assert m.calls == ["queue", "state", "url", "log"]
        assert lines(out) == ["[progress] waiting for a worker: fu-1", "[progress] containers: 1 loading image"]
        assert s.app_page_url.endswith("ap-1")
    finally:
        undo()
    assert not hasattr(FakeManager.update_task_state, "_modal_progress")


def test_run_stops_and_exits_3_on_a_crash_loop(monkeypatch, tmp_path, capsys):
    # A stand-in for `modal run`: its log loop delivers a crash-loop message.
    import modal._output.rich as rich

    from modal_progress import __main__ as cli

    monkeypatch.setattr(rich, "RichOutputManager", type("R", (FakeManager,), {}))
    exits, stops = [], []
    monkeypatch.setattr("os._exit", exits.append)
    monkeypatch.setattr(sys, "argv", list(sys.argv))
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: stops.append(cmd))

    def fake_main():
        m = rich.RichOutputManager(terminal=False)
        m.update_app_page_url("https://modal.com/apps/x/main/ap-Crash1")
        for text in ("ModuleNotFoundError: No module named 'helper'\n", "Function app.work is crash-looping: ...\n"):
            asyncio.run(m.put_streaming_log(types.SimpleNamespace(data=text)))
        for t in list(threading.enumerate()):
            if t.name == "modal-progress-stop":
                t.join(5)

    monkeypatch.setattr("modal.__main__.main", fake_main)
    app = tmp_path / "app.py"
    app.write_text("import modal\n")
    try:
        cli.run([str(app)], interval=5.0)
        code = None
    except SystemExit as e:
        code = e.code
    assert code == 3 and exits == [3]
    assert stops and stops[0][-3:] == ["stop", "-y", "ap-Crash1"]
    out = capsys.readouterr().out
    assert "crash loop: app.work keeps failing to start, last error: ModuleNotFoundError" in out
    assert "stopping ap-Crash1, exit 3" in out
