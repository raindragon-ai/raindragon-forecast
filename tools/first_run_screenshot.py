#!/usr/bin/env python3
"""Show what a brand-new install sees (zero history), from the real code.

    uv run --with playwright tools/first_run_screenshot.py [--chromium EXE]

Writes docs/screenshots/first-run-plugin.png and first-run-extension.png and
prints the exact lines. The plugin line comes from running the real hook on an
empty data dir; the extension pill from the real extension libraries computing
forecast() on an empty history, drawn over the synthetic host page
(tests/fixtures/pill/host_menu.html: no real site, no real content).
Used by docs/FIRST_RUN.md (RAI-329)."""
import argparse
import html
import json
import os
import pathlib
import subprocess
import sys
import tempfile

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screenshots"
HOOK = ROOT / "plugin" / "scripts" / "raindragon_eta_hook.py"
LIB = ROOT / "extension" / "lib"


def plugin_first_prompt() -> str:
    """The systemMessage of the first prompt on a fresh install."""
    with tempfile.TemporaryDirectory() as t:
        env = dict(os.environ, HOME=os.path.join(t, "home"), CLAUDE_PLUGIN_DATA=os.path.join(t, "data"),
                   CLAUDE_PLUGIN_OPTION_CHECK_STATUS="false")
        os.makedirs(env["HOME"])
        inp = {"session_id": "s1", "prompt_id": "p1", "prompt": "fix the failing test and run the suite",
               "transcript_path": "/nonexistent", "hook_event_name": "UserPromptSubmit"}
        r = subprocess.run([sys.executable, str(HOOK), "prompt"], input=json.dumps(inp),
                           capture_output=True, text=True, env=env, check=True)
        return json.loads(r.stdout)["systemMessage"]


TERMINAL = """<!doctype html><meta charset="utf-8"><body style="margin:0;background:#1e1e1e">
<pre style="margin:0;padding:22px 26px;font:15px/1.6 'DejaVu Sans Mono',Menlo,monospace;color:#d4d4d4">
<span style="color:#888">&gt;</span> fix the failing test and run the suite
<span style="color:#9aa0a6">{line}</span>
</pre></body>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chromium")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    line = plugin_first_prompt()
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=a.chromium) if a.chromium else p.chromium.launch()

        pg = b.new_page(viewport={"width": 760, "height": 110})
        pg.set_content(TERMINAL.format(line=html.escape(line)))
        pg.screenshot(path=str(OUT / "first-run-plugin.png"))

        pg = b.new_page(viewport={"width": 1100, "height": 560})
        pg.goto((ROOT / "tests" / "fixtures" / "pill" / "host_menu.html").as_uri())
        for f in ("turn_model_data.js", "turn_model.js", "predict.js", "pill.js"):
            pg.add_script_tag(path=str(LIB / f))
        pg.evaluate("closeMenus()")
        text = pg.evaluate("""() => {
            const T = RainDragonEta, gm = {M: RDTurnModel, data: RDTurnModelData};
            const fc = T.forecast([], 120, 0, "Opus 5.5 Medium", "50", "global", gm, null, Date.now() / 1000);
            const t = T.bandLine(fc.band);
            RainDragonEtaPill.create().show(document.getElementById('box'), t, '', false);
            return t; }""")
        pg.wait_for_timeout(100)
        pg.screenshot(path=str(OUT / "first-run-extension.png"))
        b.close()
    print("plugin:    " + line)
    print("extension: " + text)


if __name__ == "__main__":
    main()
