# The X post video and the GitHub social preview, rendered from the web demo
# frame by frame, so the timing is exact no matter how slow the machine is.
#
#   python scripts/post_video.py out_dir
import functools
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import wave

import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = os.path.join(os.path.dirname(__file__), "..")
FPS = 60
RATE = 48000

# (video seconds, trace seconds): straight lines between these points.
# Open on the finished run, dissolve to the start, skip the cold start,
# play at 4x up to the failure, then faster to the end, and hold.
HOLD, DISSOLVE = 0.85, 0.45
KEYS = [(1.30, 0.0), (1.90, 6.8), (9.70, 38.5), (13.2, 57.145), (15.0, 57.145)]

HIDE = """
header, .start, .bar-row, footer, .sim { display: none !important; }
* { transition: none !important; }
main { padding: 0 32px; min-height: 100vh; display: flex; flex-direction: column; justify-content: center; }
"""


def trace_time(v: float) -> float:
    if v <= KEYS[0][0]:
        return KEYS[0][1]
    for (v0, t0), (v1, t1) in zip(KEYS, KEYS[1:]):
        if v <= v1:
            return t0 + (t1 - t0) * (v - v0) / (v1 - v0)
    return KEYS[-1][1]


def video_time(t: float) -> float:
    for (v0, t0), (v1, t1) in zip(KEYS, KEYS[1:]):
        if t0 <= t <= t1 and t1 > t0:
            return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return KEYS[-1][0]


def serve() -> str:
    docs = os.path.abspath(os.path.join(ROOT, "docs"))
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=docs)
    handler.log_message = lambda *a: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}/"


def frames(url: str, out: str) -> int:
    total = round(KEYS[-1][0] * FPS)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 800}, device_scale_factor=1.5, color_scheme="dark")
        page.goto(url)
        page.wait_for_function("window.__duration !== undefined")
        page.add_style_tag(content=HIDE)

        def shot(t: float) -> Image.Image:
            page.evaluate(f"window.__seek({t})")
            path = os.path.join(out, "tmp.png")
            page.screenshot(path=path)
            return Image.open(path).convert("RGB")

        end, start = shot(KEYS[-1][1]), shot(0.0)
        for k in range(total):
            v = k / FPS
            if v < HOLD:
                img = end
            elif v < HOLD + DISSOLVE:
                img = Image.blend(end, start, (v - HOLD) / DISSOLVE)
            elif v < KEYS[0][0]:
                img = start
            else:
                img = shot(trace_time(v))
            img.save(os.path.join(out, f"{k:04d}.png"))
        browser.close()
    return total


def social(url: str, dst: str) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 640}, device_scale_factor=1, color_scheme="dark")
        page.goto(url)
        page.wait_for_function("window.__duration !== undefined")
        page.add_style_tag(content="""
            .start, .bar-row, footer, .sim { display: none !important; }
            * { transition: none !important; }
            main { padding: 26px 40px; max-width: 1280px; }
            header { margin-bottom: 16px; } h1 { font-size: 34px; }
            .log .body { height: 196px; }
        """)
        page.evaluate("window.__seek(40)")
        page.screenshot(path=dst)
        browser.close()


def tone(freq: float, dur: float, gain: float) -> np.ndarray:
    t = np.arange(int(dur * RATE)) / RATE
    env = np.exp(-t * 5 / dur) * np.minimum(1, t / 0.004)
    return gain * env * (np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(4 * np.pi * freq * t))


def sound(dst: str) -> None:
    trace = json.load(open(os.path.join(ROOT, "docs", "trace.json")))
    n = int(KEYS[-1][0] * RATE)
    mix = np.zeros(n + RATE)
    pent = [523.25, 587.33, 659.25, 783.99, 880.0]  # C major pentatonic

    def add(at: float, sig: np.ndarray) -> None:
        i = int(at * RATE)
        mix[i : i + len(sig)] += sig[: len(mix) - i]

    for k, (at, ok, _) in enumerate(trace["results"]):
        v = video_time(at)
        if ok:
            add(v, tone(pent[k % 5] * 2, 0.09, 0.05))  # soft tick per finished input
        else:
            add(v, tone(196.0, 0.9, 0.22) + tone(233.08, 0.9, 0.12))  # low, calm, the failure
    for f in (523.25, 659.25, 783.99):
        add(video_time(KEYS[-1][1]) - 1.75, tone(f, 1.6, 0.12))  # the finish
    for f in (523.25, 659.25, 783.99):
        add(0.0, tone(f, 0.8, 0.06))  # the opening result frame
    mix = mix[:n]
    rms = np.sqrt(np.mean(mix**2))
    mix *= 10 ** (-24 / 20) / max(rms, 1e-9)
    mix = np.clip(mix, -0.95, 0.95)
    with wave.open(dst, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((mix * 32767).astype(np.int16).tobytes())


def main(out: str) -> None:
    os.makedirs(out, exist_ok=True)
    url = serve()
    social(url, os.path.join(out, "github-social-preview.png"))
    tmp = tempfile.mkdtemp()
    total = frames(url, tmp)
    wav = os.path.join(tmp, "sound.wav")
    sound(wav)
    mp4 = os.path.join(out, "modal-progress.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", os.path.join(tmp, "%04d.png"),
                    "-i", wav, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-c:a", "aac", "-b:a", "160k",
                    "-shortest", mp4], check=True)
    shutil.copy(os.path.join(tmp, "0000.png"), os.path.join(out, "video-thumbnail.png"))
    shutil.rmtree(tmp)
    print(f"{total} frames -> {mp4}")


if __name__ == "__main__":
    main(sys.argv[1])
