"""python -m modal_progress run app.py   modal run, plus plain progress lines in logs
python -m modal_progress check app.py local modules Modal won't upload
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


# Exit code when the run is stopped because its containers keep crashing on startup.
CRASH_LOOP_EXIT = 3


def run(rest: list[str], interval: float, fail_fast: bool = True) -> None:
    import os

    import modal.__main__

    from . import preflight, signals
    from .agent import install
    from .render import PREFIX

    heartbeat = float(os.environ.get("MODAL_PROGRESS_HEARTBEAT", 30))
    app = preflight.target(rest)
    if app is not None:
        try:
            for m in preflight.check(app):
                print(f"{PREFIX} warning: {m.message(app)}", flush=True)
        except Exception:
            pass

    def stop(line: str) -> None:
        # Modal keeps restarting the containers and `modal run` keeps waiting,
        # so stop the app and exit, from a thread because this runs inside
        # Modal's log loop.
        def go() -> None:
            app_id = signals.app_id(sig.app_page_url)
            print(f"{PREFIX} stopping {app_id or 'the app'}, exit {CRASH_LOOP_EXIT} (--no-fail-fast keeps it running)", flush=True)
            if app_id:
                import subprocess

                try:
                    subprocess.run(
                        [sys.executable, "-m", "modal", "app", "stop", "-y", app_id],
                        capture_output=True,
                        timeout=60,
                    )
                except Exception:
                    pass
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(CRASH_LOOP_EXIT)

        import threading

        threading.Thread(target=go, name="modal-progress-stop", daemon=True).start()

    sig = signals.Signals(interval=interval, heartbeat=heartbeat, on_crash_loop=stop if fail_fast else None)
    try:
        from modal._output.rich import RichOutputManager

        signals.hook(RichOutputManager, sig)
        sig.start()
    except Exception:
        pass
    install(interval=interval, heartbeat=heartbeat)
    sys.argv = ["modal", "run", *rest]
    try:
        modal.__main__.main()
    finally:
        if sig.crash_loop is not None and fail_fast:
            sys.exit(CRASH_LOOP_EXIT)


def check(paths: list[str]) -> int:
    from . import preflight

    bad = 0
    for p in paths:
        for m in preflight.check(p):
            print(m.message(preflight.Path(p)))
            bad += 1
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    # Everything after `run` goes to modal untouched, flags included.
    if argv[:1] == ["run"]:
        rest = argv[1:]
        interval, fail_fast = 5.0, True
        while rest[:1] in (["--interval"], ["--no-fail-fast"]):
            if rest[0] == "--no-fail-fast":
                fail_fast, rest = False, rest[1:]
            elif len(rest) > 1:
                interval, rest = float(rest[1]), rest[2:]
            else:
                break
        return run(rest, interval, fail_fast)

    parser = argparse.ArgumentParser(prog="modal-progress")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="modal run with plain progress lines (args go to modal run)")
    c = sub.add_parser("check", help="list local modules an app imports that Modal won't upload")
    c.add_argument("paths", nargs="+")

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
    elif args.cmd == "check":
        return check(args.paths)


if __name__ == "__main__":
    sys.exit(main())
