"""The evaluation split is defined by eval/split_manifest.json. These tests fail
if the plugin's or the extension's constants drift from it, so the coverage
numbers in /forecast:stats and on the options page keep meaning the same thing."""
import json
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "plugin", "scripts"))
from raindragon_eta import __version__, predict  # noqa: E402

M = json.load(open(os.path.join(ROOT, "eval", "split_manifest.json")))
JS = open(os.path.join(ROOT, "extension", "lib", "predict.js")).read()


def js_const(name):
    m = re.search(r"const %s = ([^;]+);" % name, JS)
    assert m, name
    return m.group(1).strip()


def test_manifest_version_matches_code():
    assert M["version"] == __version__ == json.loads(js_const("VERSION"))


def test_plugin_split_matches_manifest():
    s = M["split"]
    assert (predict.RECENT, predict.MIN_READY, predict.CONFIDENT, predict.MIN_GROUP) == \
        (s["recent_window"], s["min_ready"], s["confident"], s["min_group"])
    assert {k: list(v) for k, v in predict.BANDS.items()} == M["bands"]


def test_extension_split_matches_manifest():
    s = M["split"]
    assert [int(js_const(n)) for n in ("RECENT", "MIN_READY", "CONFIDENT", "MIN_GROUP")] == \
        [s["recent_window"], s["min_ready"], s["confident"], s["min_group"]]
    bands = json.loads(js_const("BANDS"))
    assert bands == M["bands"]


def test_evaluate_uses_only_the_past():
    # a history whose durations jump at turn 40: a scorer that peeked at the
    # future would cover the jump; walk-forward must miss it
    h = [{"t": i, "dur": 10.0 if i < 40 else 1000.0, "pb": "short", "cb": "small", "ok": True}
         for i in range(41)]
    e = predict.evaluate(h, "50")
    assert e["scored"] == 41 - M["split"]["min_ready"]
    assert e["hits"] == e["scored"] - 1
