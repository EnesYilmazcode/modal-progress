import io
import json

from modal_progress import watch
from modal_progress.sim import simulate


def test_simulated_map_end_to_end(tmp_path):
    out = io.StringIO()
    trace = tmp_path / "run.jsonl"
    with watch(name="work", mode="plain", interval=0.05, out=out, record=str(trace)) as w:
        results = list(w.track(simulate(inputs=12, containers=3, steps=5, step_time=0.005, fail=(7,)), total=12))

    assert len(results) == 12
    assert sum(isinstance(r, Exception) for r in results) == 1

    lines = out.getvalue().splitlines()
    assert "[progress] work: an input failed: ValueError: bad input 7" in lines
    assert lines[-1].startswith("[progress] work finished: 12/12 done, 1 failed, ")

    assert w.state.done == 12
    assert len(w.state.bars) == 3  # one per simulated container

    rows = [json.loads(l) for l in trace.read_text().splitlines()]
    assert rows[0]["start"] and rows[0]["total"] == 12
    assert rows[-1] == {**rows[-1], "end": True, "done": 12, "failed": 1}
    assert {r["ev"]["task"] for r in rows if "ev" in r} == set(w.state.bars)


def test_off_mode_draws_nothing():
    out = io.StringIO()
    with watch(name="work", mode="off", out=out) as w:
        list(w.track(simulate(inputs=3, containers=2, steps=2, step_time=0.001, fail=()), total=3))
    assert out.getvalue() == ""
    assert w.state.done == 3


def test_mode_env_ignores_case(monkeypatch):
    from modal_progress.watch import pick_mode

    monkeypatch.setenv("MODAL_PROGRESS", "OFF")
    assert pick_mode() == "off"
