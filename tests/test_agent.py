import io

from modal_progress.agent import LineThrottle, install


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_throttle_prints_once_per_interval_and_at_the_end():
    out, clock = io.StringIO(), Clock()
    line = LineThrottle("work", interval=5, out=out, now=clock)
    for done in range(1, 61):
        clock.t = done * 0.75
        line(done, 60)
    lines = out.getvalue().splitlines()
    assert lines[-1] == "[progress] work 60/60 done, 0:45"
    assert len(lines) == 9  # every 5 s over 45 s, plus the end
    assert all(l.startswith("[progress] work ") for l in lines)


def test_no_eta_from_a_single_result():
    out, clock = io.StringIO(), Clock()
    line = LineThrottle("work", interval=0, out=out, now=clock)
    clock.t = 8
    line(1, 60)
    assert "eta" not in out.getvalue()
    clock.t = 16
    line(10, 60)
    assert "eta" in out.getvalue().splitlines()[-1]


class FakeManager:
    def __init__(self, terminal):
        self.is_terminal = terminal
        self.calls = []

    def function_progress_callback(self, tag, total):
        return lambda completed, total: self.calls.append((completed, total))


def test_install_wraps_only_when_not_a_terminal(capsys):
    undo = install(interval=0, manager_cls=FakeManager)
    try:
        piped = FakeManager(terminal=False)
        piped.function_progress_callback("work", None)(3, 10)
        tty = FakeManager(terminal=True)
        tty.function_progress_callback("work", None)(3, 10)
    finally:
        undo()
    assert piped.calls == [(3, 10)] and tty.calls == [(3, 10)]
    assert capsys.readouterr().out.count("[progress] work 3/10") == 1


def test_undo_restores_the_original():
    original = FakeManager.function_progress_callback
    install(manager_cls=FakeManager)()
    assert FakeManager.function_progress_callback is original


def test_install_twice_prints_once(capsys):
    undo = install(interval=0, manager_cls=FakeManager)
    again = install(interval=0, manager_cls=FakeManager)
    try:
        FakeManager(terminal=False).function_progress_callback("work", None)(3, 10)
    finally:
        again()
        undo()
    assert capsys.readouterr().out.count("[progress]") == 1


def test_missing_hook_is_a_no_op():
    class Renamed:
        pass

    install(manager_cls=Renamed)()
    assert not hasattr(Renamed, "function_progress_callback")


def test_renamed_terminal_flag_never_breaks_the_map():
    class NoFlag:
        def function_progress_callback(self, tag, total):
            return lambda completed, total: None

    undo = install(interval=0, manager_cls=NoFlag)
    try:
        NoFlag().function_progress_callback("work", None)(1, 2)
    finally:
        undo()


def test_real_modal_hook_still_exists():
    # Fails here, not in a user's run, when Modal moves its private hook.
    from modal._output.rich import RichOutputManager

    original = RichOutputManager.function_progress_callback
    undo = install()
    try:
        assert RichOutputManager.function_progress_callback is not original
    finally:
        undo()
    assert RichOutputManager.function_progress_callback is original


def test_a_held_back_count_is_printed_later():
    # 3/4 arrives 2 s after 2/4 and nothing follows for 40 s. It used to be lost.
    out, clock = io.StringIO(), Clock()
    line = LineThrottle("chunk", interval=5, out=out, now=clock)
    clock.t = 279
    line(1, 4)
    clock.t = 366
    line(2, 4)
    clock.t = 368
    line(3, 4)
    clock.t = 371
    line.poll()
    assert out.getvalue().splitlines()[-1].startswith("[progress] chunk 3/4 done (75%), 6:11 elapsed")


def test_heartbeat_while_nothing_changes():
    out, clock = io.StringIO(), Clock()
    line = LineThrottle("work", interval=5, out=out, now=clock, heartbeat=30)
    line(0, 4)
    for t in range(1, 95):
        clock.t = t
        line.poll()
    assert [l.split(", ")[1] for l in out.getvalue().splitlines()] == ["0:05 elapsed", "0:35 elapsed", "1:05 elapsed"]


def test_eta_leaves_out_the_cold_start():
    out, clock = io.StringIO(), Clock()
    line = LineThrottle("work", interval=0, out=out, now=clock)
    clock.t = 240  # four minutes of cold start
    line(1, 10)
    clock.t = 250
    line(2, 10)
    # one input per 10 s since the first, 8 to go
    assert out.getvalue().splitlines()[-1].endswith("eta 1:20")


def test_a_watcher_takes_the_count_instead_of_printing(capsys):
    from modal_progress import agent, watch

    undo = install(interval=0, manager_cls=FakeManager, poll=False)
    out = io.StringIO()
    try:
        with watch(name="work", mode="plain", out=out) as w:
            # The watcher hooks the real manager classes; feed it through the fake one.
            unhook = agent.hook(FakeManager) if not getattr(FakeManager.function_progress_callback, "_modal_progress", False) else (lambda: None)

            def results():
                update = FakeManager(terminal=False).function_progress_callback("work", None)
                update(4, 4)
                yield from range(4)

            list(w.track(results(), total=4))
            unhook()
    finally:
        undo()
    assert w.state.remote == 4
    assert "[progress] work 4/4" not in capsys.readouterr().out
    assert out.getvalue().splitlines()[-1].startswith("[progress] work finished: 4/4 done")


def test_a_watcher_ignores_counters_before_it_tracks():
    from modal_progress import agent, watch

    with watch(name="work", mode="off") as w:
        assert all(listener("other") is None for listener in agent._listeners)
    assert agent._listeners == []
