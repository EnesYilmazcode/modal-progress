"""python -m modal_progress run app.py   modal run, plus plain progress lines in logs
python -m modal_progress demo         a simulated fan-out, no Modal account needed
"""

from __future__ import annotations

import argparse
import sys


def demo(args: argparse.Namespace) -> None:
    from .sim import simulate
    from .watch import watch

    fail = tuple(int(x) for x in args.fail.split(",") if x)
    with watch(name="work", mode=args.mode, interval=args.interval) as w:
        for _ in w.track(
            simulate(inputs=args.inputs, containers=args.containers, steps=args.steps, step_time=args.step_time, fail=fail),
            total=args.inputs,
        ):
            pass


def run(rest: list[str], interval: float) -> None:
    import modal.__main__

    from .agent import install

    install(interval=interval)
    sys.argv = ["modal", "run", *rest]
    modal.__main__.main()


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    # Everything after `run` goes to modal untouched, flags included.
    if argv[:1] == ["run"]:
        rest = argv[1:]
        interval = 5.0
        if rest[:1] == ["--interval"] and len(rest) > 1:
            interval, rest = float(rest[1]), rest[2:]
        return run(rest, interval)

    parser = argparse.ArgumentParser(prog="modal-progress")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="modal run with plain progress lines (args go to modal run)")

    d = sub.add_parser("demo", help="simulate a fan-out locally")
    d.add_argument("--inputs", type=int, default=60)
    d.add_argument("--containers", type=int, default=6)
    d.add_argument("--steps", type=int, default=40)
    d.add_argument("--step-time", type=float, default=0.1)
    d.add_argument("--fail", default="37", help="comma-separated inputs that raise")
    d.add_argument("--mode", choices=["rich", "plain"], default=None)
    d.add_argument("--interval", type=float, default=None, help="seconds between plain lines")

    args = parser.parse_args(argv)
    if args.cmd == "demo":
        demo(args)


if __name__ == "__main__":
    sys.exit(main())
