from modal_progress.state import STALE_AFTER, State, clock


def ev(id, task, n, total=10, done=False):
    return {"id": id, "task": task, "desc": f"input {id}", "n": n, "total": total, "done": done}


def test_latest_bar_per_container_wins():
    s = State(started=0)
    s.apply(ev("a", "t1", 3), now=1)
    s.apply(ev("b", "t1", 1), now=2)
    assert len(s.bars) == 1
    assert s.bars["t1"].id == "b"
    assert s.bars["t1"].n == 1


def test_busy_counts_only_unfinished_bars():
    s = State(started=0)
    s.apply(ev("a", "t1", 3), now=1)
    s.apply(ev("b", "t2", 10, done=True), now=1)
    assert s.busy(now=2) == 1


def test_silent_containers_drop_off():
    s = State(started=0)
    s.apply(ev("a", "t1", 3), now=1)
    assert s.active(now=1 + STALE_AFTER + 1) == []


def test_effective_progress_counts_partial_inputs():
    s = State(started=0, total=4)
    s.result(True)
    s.apply(ev("a", "t1", 5), now=1)  # half done
    assert s.effective(now=1) == 1.5


def test_eta_from_effective_progress():
    s = State(started=0, total=4)
    s.result(True)
    s.result(True)
    # 2 of 4 in 10 s, so 10 s to go
    assert s.eta(now=10) == 10


def test_eta_unknown_before_any_progress():
    s = State(started=0, total=4)
    assert s.eta(now=5) is None


def test_failures_keep_last_error():
    s = State(started=0)
    s.result(False, "ValueError: bad input 37")
    assert (s.done, s.failed, s.last_error) == (1, 1, "ValueError: bad input 37")


def test_clock():
    assert clock(7) == "0:07"
    assert clock(221) == "3:41"
    assert clock(3729) == "1:02:09"
    assert clock(None) == "?"


def test_eta_ignores_cold_start():
    s = State(started=0, total=4)
    s.apply({"id": "a", "task": "t1", "n": 0, "total": 10}, now=10)  # first container up at 10 s
    s.result(True)
    s.result(True)
    # 2 of 4 in the 10 s since the first container started, not in 20 s
    assert s.eta(now=20) == 10


def test_no_eta_before_five_percent():
    s = State(started=0, total=60)
    s.result(True)
    s.result(True)
    assert s.eta(now=10) is None


def test_outer_bar_does_not_reset_the_inner_one():
    s = State(started=0)
    s.apply(ev("outer", "t1", 0), now=1)
    s.apply(ev("inner", "t1", 4), now=2)
    s.apply(ev("outer", "t1", 1), now=3)
    assert s.bars["t1"].id == "inner" and s.bars["t1"].n == 4
    s.apply(ev("inner", "t1", 10, done=True), now=4)
    s.apply(ev("outer", "t1", 2), now=5)
    assert s.bars["t1"].id == "outer"
