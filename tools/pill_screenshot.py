#!/usr/bin/env python3
"""Screenshot the pill over a synthetic host page with two menus open
(tests/fixtures/pill/host_menu.html: no real site, no real content).

    uv run --with playwright tools/pill_screenshot.py out.png [--closed] [--pill PATH] [--chromium EXE]

--closed closes the menus first; --pill picks another pill.js (e.g. an older
copy, for a before/after); --chromium uses a local Chromium build."""
import argparse
import pathlib

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEXT = "Forecast 20s–1m · 300–800 words · still learning · 0:12"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--closed", action="store_true")
    ap.add_argument("--pill", default=str(ROOT / "extension" / "lib" / "pill.js"))
    ap.add_argument("--chromium")
    a = ap.parse_args()
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=a.chromium) if a.chromium else p.chromium.launch()
        pg = b.new_page(viewport={"width": 1100, "height": 560})
        pg.goto((ROOT / "tests" / "fixtures" / "pill" / "host_menu.html").as_uri())
        pg.add_script_tag(path=a.pill)
        if a.closed:
            pg.evaluate("closeMenus()")
        pg.evaluate("t => RainDragonEtaPill.create().show(document.getElementById('box'), t, '', false)", TEXT)
        pg.wait_for_timeout(100)
        pg.screenshot(path=a.out)
        b.close()


if __name__ == "__main__":
    main()
