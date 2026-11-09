"""Rebuild docs/demo.gif -- the demo page, driven by a browser and recorded frame by frame.

    pip install -e ".[dev]" selenium pillow
    python docs/make_demo_gif.py            # needs Chrome; no API key, no network

It serves the page on a free local port with the bundled sample rule set and the
recorded explanations, types a case, checks it, then draws a random case and checks
that. Everything on screen is the real page answering real requests.
"""
from __future__ import annotations

import argparse
import io
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from PIL import Image
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from tardoc_validator.explain import Explainer, load_recordings, recordings_path
from tardoc_validator.rules import RuleSet
from tardoc_validator.web import SAMPLE, App, handler

OUT = Path(__file__).resolve().parent / "demo.gif"
TYPED = "AA.00.0010\nAA.00.0030\nAA.00.0020 x20"
DAY = "2026-03-02"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7, help="the random case the second scene draws")
    ap.add_argument("--width", type=int, default=820, help="the GIF's width in pixels")
    a = ap.parse_args()

    rules = RuleSet.load(SAMPLE)
    app = App(rules, seed=a.seed, explainer=Explainer(rules, load_recordings(recordings_path(SAMPLE))))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(app))
    threading.Thread(target=server.serve_forever, daemon=True).start()

    opts = webdriver.ChromeOptions()
    for arg in ("--headless=new", "--window-size=1000,900", "--hide-scrollbars", "--force-device-scale-factor=1"):
        opts.add_argument(arg)
    d = webdriver.Chrome(options=opts)
    frames: list[tuple[Image.Image, int]] = []

    def snap(ms: int) -> None:
        frames.append((Image.open(io.BytesIO(d.get_screenshot_as_png())).convert("RGB"), ms))

    def click(css: str) -> None:
        d.find_element(By.CSS_SELECTOR, css).click()

    def checked() -> None:
        click("#check")
        WebDriverWait(d, 10).until(lambda _: d.find_element(By.CSS_SELECTOR, "#result .verdict"))
        d.execute_script("document.querySelector('#result').scrollIntoView({block:'end'})")

    try:
        d.get(f"http://127.0.0.1:{server.server_port}/")
        WebDriverWait(d, 10).until(lambda _: "positions loaded" in d.find_element(By.ID, "info").text)
        d.execute_script(f"document.querySelector('#day').value='{DAY}';"
                         "document.querySelector('#rows').innerHTML=''")
        snap(1100)
        box = d.find_element(By.ID, "quick")
        for i, ch in enumerate(TYPED):
            box.send_keys(ch)
            if ch == "\n" or i % 3 == 2:
                snap(80)
        snap(500)
        click("#add")
        snap(700)
        checked()
        snap(4200)

        d.execute_script("window.scrollTo(0,0)")
        click("#random")
        WebDriverWait(d, 10).until(lambda _: d.find_elements(By.CSS_SELECTOR, "#rows tr"))
        d.execute_script("document.querySelector('#rows').scrollIntoView({block:'center'})")
        snap(1300)
        checked()
        snap(4800)
    finally:
        d.quit()
        server.shutdown()

    write_gif(frames, a.width)


def write_gif(frames: list[tuple[Image.Image, int]], width: int) -> None:
    merged: list[tuple[Image.Image, int]] = []
    for img, ms in frames:  # a frame identical to the last one only lengthens it
        if merged and img.tobytes() == merged[-1][0].tobytes():
            merged[-1] = (merged[-1][0], merged[-1][1] + ms)
        else:
            merged.append((img, ms))
    scale = width / merged[0][0].width
    size = (width, round(merged[0][0].height * scale))
    small = [(img.resize(size, Image.LANCZOS), ms) for img, ms in merged]
    palette = small[-1][0].quantize(colors=96, method=Image.Quantize.MEDIANCUT)
    images = [img.quantize(palette=palette, dither=Image.Dither.NONE) for img, _ in small]
    images[0].save(OUT, save_all=True, append_images=images[1:], duration=[ms for _, ms in small],
                   loop=0, optimize=True, disposal=1)
    total = sum(ms for _, ms in small) / 1000
    kib = OUT.stat().st_size / 1024
    print(f"{OUT.name}: {len(images)} frames, {total:.1f} s, {kib:.0f} KiB, {size[0]}x{size[1]}")


if __name__ == "__main__":
    main()
