"""The global turn model: the file, its features, which band is shown, and the hook."""
import io
import json
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts"))

import raindragon_eta_hook as hook  # noqa: E402
from raindragon_eta import predict, store, turn_model  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "turn_model", "cases.json")


def rec(dur, **kw):
    return dict({"t": 0, "dur": dur, "pb": "short", "cb": "small", "effort": "high", "ok": True,
                 "incident": False}, **kw)


# ---------- the file ----------

def test_model_file_loads_and_wants_only_known_features():
    m = turn_model.load()
    assert m and list(m["features"]) == list(turn_model.FEATURES)
    assert len(m["trees"]) == len(turn_model.QUANTILES)


def test_extension_bundles_the_same_model():
    """extension/lib/turn_model_data.js is tools/bundle_turn_model.py's copy of turn_model.json."""
    path = os.path.join(os.path.dirname(__file__), "..", "extension", "lib", "turn_model_data.js")
    with open(path) as f:
        text = f.read()
    start = text.index("root.RDTurnModelData = ") + len("root.RDTurnModelData = ")
    bundled = json.loads(text[start:text.rindex("; })")])
    with open(turn_model.MODEL_PATH) as f:
        assert bundled == json.load(f)


def test_model_file_holds_numbers_not_text():
    """No prompt text, ids or other strings: only its own keys and feature names."""
    with open(turn_model.MODEL_PATH) as f:
        raw = json.load(f)
    allowed = set(turn_model.FEATURES) | {raw["version"], raw["target"]}
    strings = []

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str):
            strings.append(v)
    walk(raw)
    assert set(strings) <= allowed


def test_python_walk_matches_the_fixture():
    m = turn_model.load()
    with open(FIX) as f:
        fx = json.load(f)
    assert fx["model_version"] == m["version"]
    for c in fx["cases"]:
        f = turn_model.features(c["prompt_chars"], c["transcript_bytes"], c["effort"], c["history"],
                                c["session"], c["now"])
        assert turn_model.quantiles(m, f) == pytest.approx(c["quantiles"], abs=1e-12)


def test_missing_or_broken_file_means_no_model(tmp_path):
    assert turn_model.load(str(tmp_path / "nope.json")) is None
    bad = tmp_path / "bad.json"
    bad.write_text('{"features": ["prompt_text"], "quantiles": [0.1, 0.25, 0.5, 0.75, 0.9]}')
    assert turn_model.load(str(bad)) is None
    assert turn_model.band(None, {}, 0) is None


# ---------- features ----------

def test_first_turn_has_a_band_and_80_holds_50():
    m = turn_model.load()
    f = turn_model.features(120, 0, None, [], None, 1000)
    b50, b80 = turn_model.band(m, f, 0, "50"), turn_model.band(m, f, 0, "80")
    assert 0 < b50.low < b50.high and b80.low <= b50.low and b80.high >= b50.high
    assert b50.group == "global" and not b50.learning


def test_session_features_keep_counting_past_the_kept_turns():
    done = [[100.0 * i, 10.0 + i] for i in range(1, 11)]
    f = turn_model.session_features({"n": 25, "k": 24, "done": done}, 1100.0)
    assert f["s_n"] == 24 and f["lprior"] == pytest.approx(math.log1p(25))
    assert f["s_prev1"] == pytest.approx(math.log(20.0)) and f["s_prev2"] == pytest.approx(math.log(19.0))
    assert f["s_gap"] == pytest.approx(math.log1p(100.0))


def test_session_turns_ending_later_are_not_read():
    f = turn_model.session_features({"n": 1, "k": 1, "done": [[500.0, 30.0]]}, 400.0)
    assert f["s_n"] == 0 and math.isnan(f["s_prev1"])


def test_user_quantiles_need_three_turns_and_ignore_failures():
    f = turn_model.features(10, 0, "high", [rec(30), rec(40), rec(999, ok=False)], None, 0)
    assert f["u_n"] == 2 and math.isnan(f["u_med"])
    f = turn_model.features(10, 0, "high", [rec(30), rec(40), rec(50)], None, 0)
    assert f["u_med"] == pytest.approx(math.log(40))


# ---------- which band is shown ----------

def _band(lo, hi, learning=False, group="x"):
    return predict.Band(lo, hi, 50, group, "50", learning)


def test_choose_global_while_local_is_learning_or_absent():
    g = _band(10, 40, group="global")
    assert turn_model.choose(g, None, []) == (g, "global")
    assert turn_model.choose(g, _band(1, 2, learning=True), []) == (g, "global")
    assert turn_model.choose(None, _band(1, 2), []) == (_band(1, 2), "local")


def test_choose_local_only_when_it_was_right_more_often_lately():
    g, loc = _band(10, 40, group="global"), _band(1, 2)
    good_local = [rec(30, glb=[1000, 4000], loc=[20, 45])] * turn_model.PICK_MIN
    assert turn_model.choose(g, loc, good_local) == (loc, "local")
    tie = [rec(30, glb=[20, 45], loc=[20, 45])] * turn_model.PICK_MIN
    assert turn_model.choose(g, loc, tie) == (g, "global")
    few = good_local[:turn_model.PICK_MIN - 1]
    assert turn_model.choose(g, loc, few) == (g, "global")


def test_evaluate_live_scores_both_bands():
    h = [rec(30, glb=[20, 45], loc=[100, 200]), rec(30, glb=[20, 45], loc=[25, 35], shown="local")]
    ev = turn_model.evaluate_live(h)
    assert ev["global"]["coverage"] == 1.0 and ev["local"]["coverage"] == 0.5
    assert ev["shown"]["scored"] == 2


# ---------- hook end to end ----------

def run(event, payload, env, now):
    out = io.StringIO()
    assert hook.main(["hook", event], io.StringIO(json.dumps(payload)), out, env, now=now) == 0
    return json.loads(out.getvalue()) if out.getvalue() else None


@pytest.fixture
def env(tmp_path):
    return {"CLAUDE_PLUGIN_DATA": str(tmp_path), "CLAUDE_PLUGIN_OPTION_CHECK_STATUS": "false",
            "HOME": str(tmp_path / "home")}


def prompt(i, sid="s1", text="fix the test"):
    return {"session_id": sid, "prompt_id": "p%d" % i, "prompt": text, "effort": {"level": "high"},
            "transcript_path": "/nonexistent"}


def test_first_prompt_shows_the_global_band(env):
    out = run("prompt", prompt(1), env, 1000)
    assert "of turns like this" in out["systemMessage"] and "learning" not in out["systemMessage"]
    p = json.load(open(os.path.join(env["CLAUDE_PLUGIN_DATA"], "pending", "s1")))
    assert p["shown"] == "global" and p["glb"][0] < p["glb"][1] and p["loc"] is None


def test_stop_records_both_bands_and_the_session_turn(env):
    run("prompt", prompt(1), env, 1000)
    run("stop", {"session_id": "s1", "prompt_id": "p1"}, env, 1042)
    h = store.read_history(env["CLAUDE_PLUGIN_DATA"])
    assert len(h) == 1 and h[0]["glb"] and "loc" not in h[0]
    s = store.read_session(env["CLAUDE_PLUGIN_DATA"], "s1")
    assert s == {"n": 1, "k": 1, "done": [[1042.0, 42.0]]}


def test_second_turn_reads_its_session(env, monkeypatch):
    seen = []
    real = turn_model.features
    monkeypatch.setattr(turn_model, "features", lambda *a: seen.append(a[4]) or real(*a))
    run("prompt", prompt(1), env, 1000)
    run("stop", {"session_id": "s1", "prompt_id": "p1"}, env, 1042)
    run("prompt", prompt(2), env, 1100)
    assert seen[1]["k"] == 1 and seen[1]["done"] == [[1042.0, 42.0]]


def test_history_still_holds_no_prompt_text(env):
    run("prompt", prompt(1, text="SECRET-PROMPT-TEXT"), env, 1000)
    run("stop", {"session_id": "s1", "prompt_id": "p1"}, env, 1030)
    d = env["CLAUDE_PLUGIN_DATA"]
    for root, _, files in os.walk(d):
        for name in files:
            assert "SECRET" not in open(os.path.join(root, name)).read()


def test_model_local_option_brings_back_the_learning_rule(env):
    out = run("prompt", prompt(1), dict(env, CLAUDE_PLUGIN_OPTION_MODEL="local"), 1000)
    assert "learning your pace" in out["systemMessage"]


def test_without_a_model_file_it_falls_back_to_local(env, monkeypatch):
    monkeypatch.setattr(turn_model, "load", lambda *a: None)
    out = run("prompt", prompt(1), env, 1000)
    assert "learning your pace" in out["systemMessage"]


def test_doctor_names_the_model(env):
    run("prompt", prompt(1), env, 1000)
    out = io.StringIO()
    hook.main(["hook", "doctor"], io.StringIO(""), out, env, now=1001)
    doc = json.loads(out.getvalue())
    assert doc["model"]["setting"] == "global" and doc["model"]["file"] is True
