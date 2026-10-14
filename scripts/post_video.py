# The X post video (no sound) and the GitHub social preview, rendered from scripts/render/page.html
# frame by frame, so the timing is exact no matter how slow the machine is.
#
#   python scripts/post_video.py out_dir
import functools
import http.server
import os
import shutil
import subprocess
import sys
import tempfile
import threading

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = os.path.join(os.path.dirname(__file__), "..")
FPS = 60

# (video seconds, trace seconds): straight lines between these points.
# Open on the finished run, dissolve to the start, skip the cold start,
# play at 4x up to the failure, then faster to the end, and hold.
HOLD, DISSOLVE = 0.85, 0.45
KEYS = [(1.30, 0.0), (1.90, 6.8), (9.70, 38.5), (13.2, 57.145), (15.0, 57.145)]

HIDE = """
header, .start, .bar-row, footer, .sim, .explain, .term:not(.log) { display: none !important; }
* { transition: none !important; }
html, body { background: #1c211e; }
main { padding: 0 !important; max-width: none !important; margin: 0 !important; }
.logs { margin: 0 !important; gap: 2px; height: 100vh; }
.term { border: 0 !important; border-radius: 0 !important; }
.term .title { font-size: 16px; color: #c9d1cb; padding: 10px 16px; }
.log .body { height: calc(100vh - 43px) !important; font-size: 16px; padding: 12px 16px; }
"""


def trace_time(v: float) -> float:
    if v <= KEYS[0][0]:
        return KEYS[0][1]
    for (v0, t0), (v1, t1) in zip(KEYS, KEYS[1:]):
        if v <= v1:
            return t0 + (t1 - t0) * (v - v0) / (v1 - v0)
    return KEYS[-1][1]


def serve() -> str:
    docs = os.path.abspath(os.path.join(ROOT, "scripts", "render"))
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=docs)
    handler.log_message = lambda *a: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}/page.html"


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
            .start, .bar-row, footer, .sim, .explain, .term:not(.log) { display: none !important; }
            * { transition: none !important; }
            main { padding: 26px 40px; max-width: 1280px; }
            header { margin-bottom: 16px; } h1 { font-size: 34px; }
            .logs { margin-top: 0; } .log .body { height: 400px; }
        """)
        page.evaluate("window.__seek(40)")
        page.screenshot(path=dst)
        browser.close()


def main(out: str) -> None:
    os.makedirs(out, exist_ok=True)
    url = serve()
    social(url, os.path.join(out, "github-social-preview.png"))
    tmp = tempfile.mkdtemp()
    total = frames(url, tmp)
    mp4 = os.path.join(out, "modal-progress.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", os.path.join(tmp, "%04d.png"),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-an", mp4], check=True)
    shutil.copy(os.path.join(tmp, "0000.png"), os.path.join(out, "video-thumbnail.png"))
    shutil.rmtree(tmp)
    print(f"{total} frames -> {mp4}")


if __name__ == "__main__":
    main(sys.argv[1])
