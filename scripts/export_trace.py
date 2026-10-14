# Turns a run recorded with watch(record=...) into the compact JSON the web
# demo replays.
#
#   python scripts/export_trace.py run.jsonl docs/trace.json
import json
import sys


def main(src: str, dst: str) -> None:
    rows = [json.loads(line) for line in open(src, encoding="utf8")]
    start = next(r for r in rows if r.get("start"))
    end = next(r for r in rows if r.get("end"))
    containers: list[str] = []
    events, results = [], []
    for r in rows:
        if "ev" in r:
            ev = r["ev"]
            if ev["task"] not in containers:
                containers.append(ev["task"])
            i = int(ev["desc"].split()[-1])
            events.append([r["at"], containers.index(ev["task"]), i, ev["n"], ev["total"], int(ev["done"])])
        elif "result" in r:
            results.append([r["at"], int(r["result"]), r["error"]])
    out = {
        "name": start["name"],
        "total": start["total"],
        "duration": end["at"],
        "containers": containers,
        "events": events,
        "results": results,
    }
    with open(dst, "w", encoding="utf8") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"{len(events)} events, {len(results)} results, {len(containers)} containers, {end['at']:.1f} s -> {dst}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
