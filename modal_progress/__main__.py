"""python -m modal_progress demo     a simulated fan-out, no Modal account needed"""

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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="modal-progress")
    sub = parser.add_subparsers(dest="cmd", required=True)

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
