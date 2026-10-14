# Run `modal run` with stdout to a file (like a background agent does) and
# print what the file contains every 5 s.
import subprocess, sys, time, os
log = sys.argv[1]
cmd = [sys.executable, "-X", "utf8", "-m", "modal", "run", "repro/slow_map.py"]
with open(log, "w", encoding="utf8") as f:
    p = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, env={**os.environ, "PYTHONIOENCODING": "utf8"})
    t = time.time()
    while p.poll() is None:
        time.sleep(5)
        data = open(log, encoding="utf8", errors="replace").read()
        last = data.strip().splitlines()[-1][:110] if data.strip() else "<empty>"
        print(f"t={time.time()-t:4.0f}s  bytes={len(data):5d}  last line: {last}", flush=True)
print("exit", p.returncode)
