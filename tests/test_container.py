import pytest

from modal_progress import bar, container, report


@pytest.fixture
def sent():
    events = []
    container.set_local_sink(events.append)
    yield events
    container.set_local_sink(None)


def test_bar_sends_first_and_last(sent):
    for _ in bar(range(1000), desc="x", every=60):
        pass
    container._sender.flush()
    assert [e["n"] for e in sent] == [1000]  # first and last coalesce to the latest
    assert sent[-1]["done"] is True
    assert sent[-1]["total"] == 1000


def test_bar_throttles(sent, monkeypatch):
    t = [0.0]
    monkeypatch.setattr(container.time, "monotonic", lambda: t[0])
    b = bar(total=10, every=1.0)
    container._sender.flush()
    for _ in range(10):
        t[0] += 0.25
        b.update()
        container._sender.flush()
    b.close()
    # Start, then once per simulated second, then the close.
    assert [e["n"] for e in sent] == [0, 4, 8, 10]


def test_bar_closes_when_loop_breaks(sent):
    for i in bar(range(10)):
        if i == 3:
            break
    assert sent[-1]["done"] is True
    assert sent[-1]["n"] == 3


def test_off_sends_nothing(sent, monkeypatch):
    monkeypatch.setenv("MODAL_PROGRESS", "off")
    for _ in bar(range(5)):
        pass
    container._sender.flush()
    assert sent == []


def test_report_closes_at_total(sent):
    report(5, total=10, desc="manual")
    report(10, total=10)
    assert sent[-1]["done"] is True
    assert sent[-1]["desc"] == "manual"


def test_sink_errors_never_reach_the_job():
    def broken(ev):
        raise RuntimeError("queue down")

    container.set_local_sink(broken)
    try:
        for _ in bar(range(3)):
            pass
    finally:
        container.set_local_sink(None)
