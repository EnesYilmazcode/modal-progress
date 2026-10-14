# Screenshots and the README GIF, taken from the web demo in docs/.
#
#   python scripts/capture_demo.py shots out_dir        a few stills to review
#   python scripts/capture_demo.py gif assets/media/demo.gif   also writes demo-end.png
import functools
import http.server
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from playwright.sync_api import sync_playwright

DOCS = os.path.join(os.path.dirname(__file__), "..", "docs")


def serve() -> str:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=os.path.abspath(DOCS))
    handler.log_message = lambda *a: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}/"


def page_for(p, url, width, height, scheme="dark"):
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1.5, color_scheme=scheme)
    page.goto(url)
    return browser, page


def shots(out: str) -> None:
    os.makedirs(out, exist_ok=True)
    url = serve()
    with sync_playwright() as p:
        for scheme in ("dark", "light"):
            browser, page = page_for(p, url, 1180, 900, scheme)
            page.screenshot(path=f"{out}/start-{scheme}.png")
            browser.close()
        browser, page = page_for(p, url + "?autoplay=1&speed=4", 1180, 1000)
        for at in (3, 7, 10, 15):
            page.wait_for_timeout(at * 1000 - (page.evaluate("performance.now()") - 0))
            page.screenshot(path=f"{out}/play-{at}s.png", full_page=True)
        browser.close()
        browser, page = page_for(p, url + "?autoplay=1&speed=4", 390, 900)
        page.wait_for_timeout(9000)
        page.screenshot(path=f"{out}/phone.png", full_page=True)
        browser.close()


def gif(dst: str, fps: int = 10, speed: int = 4) -> None:
    url = serve()
    frames = tempfile.mkdtemp()
    with sync_playwright() as p:
        browser, page = page_for(p, url + f"?autoplay=1&speed={speed}", 1080, 590)
        page.add_style_tag(content="header,.start,.bar-row,footer{display:none!important} main{padding:16px}")
        duration = page.evaluate("fetch('trace.json').then(r=>r.json()).then(t=>t.duration)") / speed + 2.5
        t0 = time.monotonic()
        k = 0
        while time.monotonic() - t0 < duration:
            page.screenshot(path=f"{frames}/{k:04d}.png")
            k += 1
            wait = t0 + k / fps - time.monotonic()
            if wait > 0:
                time.sleep(wait)
        browser.close()
    palette = f"{frames}/palette.png"
    scale = "fps=10,scale=900:-1:flags=lanczos"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps), "-i", f"{frames}/%04d.png",
                    "-vf", f"{scale},palettegen=max_colors=64", palette], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps), "-i", f"{frames}/%04d.png",
                    "-i", palette, "-lavfi", f"{scale}[x];[x][1:v]paletteuse=dither=none", dst], check=True)
    still = os.path.splitext(dst)[0] + "-end.png"
    shutil.copy(f"{frames}/{k - 1:04d}.png", still)
    shutil.rmtree(frames)
    print(f"{k} frames -> {dst} ({os.path.getsize(dst) // 1024} KB)")


if __name__ == "__main__":
    {"shots": shots, "gif": gif}[sys.argv[1]](sys.argv[2])
