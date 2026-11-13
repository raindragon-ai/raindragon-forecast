import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts"))

import raindragon_eta_hook as hook  # noqa: E402
from raindragon_eta import fmt, predict, server, status, store  # noqa: E402

MARK = "\u260a\u260b "  # the Dragon's Head and Tail mark that starts every line


def rec(dur, ok=True, incident=False, pb="short", cb="small", effort="high"):
    return {"t": 0, "dur": dur, "pb": pb, "cb": cb, "effort": effort, "ok": ok, "incident": incident}


# ---------- predict ----------

def test_no_band_before_min_ready():
    hist = [rec(30)] * (predict.MIN_READY - 1)
    assert predict.predict(hist, 10, 0, "high") is None


def test_failed_and_incident_turns_never_count():
    hist = [rec(30)] * (predict.MIN_READY - 1) + [rec(900, ok=False)] * 20 + [rec(900, incident=True)] * 20
    assert predict.predict(hist, 10, 0, "high") is None
    hist.append(rec(30))
    b = predict.predict(hist, 10, 0, "high")
    assert b.low == b.high == 30


def test_band_is_middle_half():
    hist = [rec(d) for d in range(1, 101)]  # 1..100 seconds
    b = predict.predict(hist, 10, 0, "high")
    assert (round(b.low, 2), round(b.high, 2)) == (25.75, 75.25)
    assert b.group == "prompt+context+effort" and b.n == 100


def test_eighty_band_is_wider():
    hist = [rec(d) for d in range(1, 101)]
    b50 = predict.predict(hist, 10, 0, "high", "50")
    b80 = predict.predict(hist, 10, 0, "high", "80")
    assert b80.low < b50.low and b80.high > b50.high


def test_falls_back_to_broader_group():
    # 20 long-prompt turns but only 3 short ones: a short prompt uses effort-level history
    hist = [rec(300, pb="long")] * 20 + [rec(10, pb="short")] * 3
    b = predict.predict(hist, 10, 0, "high")
    assert b.group == "effort" and b.n == 23


def test_uses_recent_turns_only():
    hist = [rec(1000)] * 500 + [rec(20)] * predict.RECENT
    b = predict.predict(hist, 10, 0, "high")
    assert b.high == 20


@pytest.mark.parametrize("vals,q,want", [([5], 0.25, 5), ([1, 3], 0.5, 2), ([1, 2, 3, 4, 5], 0.75, 4)])
def test_quantile(vals, q, want):
    assert predict.quantile(vals, q) == want


# ---------- fmt ----------

@pytest.mark.parametrize("s,want", [(0, "0s"), (44.6, "45s"), (60, "1m"), (95, "1m30s"), (130, "2m"),
                                    (659, "11m"), (3600, "1h"), (3720, "1h02m"), (7190, "2h")])
def test_duration(s, want):
    assert fmt.duration(s) == want


def test_band_line_collapses_equal_ends():
    assert fmt.band_line(30, 30, 12, "50") == "Forecast 30s · half of your similar turns (12)"
    assert fmt.band_line(40, 180, 15, "80", learning=True) == \
        "Forecast 40s–3m · 8 in 10 of your similar turns (15, still learning)"


def test_done_line_says_where_the_turn_landed():
    assert fmt.done_line(72, 40, 180) == "done in 1:12 · inside the forecast"
    assert fmt.done_line(200, 40, 180) == "done in 3:20 · longer than the forecast (40s–3m)"
    assert fmt.done_line(5, 40, 180) == "done in 0:05 · quicker than the forecast (40s–3m)"
    assert fmt.done_line(75, None, None) == "done in 1:15"


# ---------- status ----------

LIVE_SHAPE = {  # shape of status.claude.com on 2026-10-07: Console down, Code fine
    "components": [{"name": "claude.ai", "status": "operational"},
                   {"name": "Claude Console (platform.claude.com)", "status": "partial_outage"},
                   {"name": "Claude API (api.anthropic.com)", "status": "operational"},
                   {"name": "Claude Code", "status": "operational"}],
    "incidents": [{"name": "Elevated errors on platform.claude.com", "status": "investigating",
                   "components": [{"name": "Claude Console (platform.claude.com)"}]}],
}


def test_console_incident_is_not_a_claude_code_incident():
    assert status.incident_from_summary(LIVE_SHAPE, status.CLAUDE_CODE_COMPONENTS) is None


def test_api_incident_is_reported():
    s = json.loads(json.dumps(LIVE_SHAPE))
    s["incidents"].append({"name": "Elevated errors on Claude Opus", "status": "identified", "impact": "major",
                           "components": [{"name": "Claude API (api.anthropic.com)"}]})
    assert status.incident_from_summary(s, status.CLAUDE_CODE_COMPONENTS) == "Elevated errors on Claude Opus"


def test_degraded_component_is_not_an_incident():
    # measured: degraded/minor periods did not slow turns (0.91x), so no warning
    s = json.loads(json.dumps(LIVE_SHAPE))
    s["components"][3]["status"] = "degraded_performance"
    assert status.incident_from_summary(s, status.CLAUDE_CODE_COMPONENTS) is None
    s["components"][3]["status"] = "partial_outage"
    assert status.incident_from_summary(s, status.CLAUDE_CODE_COMPONENTS) is None


def test_major_outage_component_is_reported():
    s = json.loads(json.dumps(LIVE_SHAPE))
    s["components"][3]["status"] = "major_outage"
    assert status.incident_from_summary(s, status.CLAUDE_CODE_COMPONENTS) == "Claude Code: major outage"


@pytest.mark.parametrize("impact,reported", [("none", False), ("minor", False), (None, False),
                                             ("major", True), ("critical", True)])
def test_only_major_incidents_count(impact, reported):
    inc = {"name": "Slow Claude Code", "status": "investigating",
           "components": [{"name": "Claude Code"}]}
    if impact is not None:
        inc["impact"] = impact
    got = status.incident_from_summary({"components": [], "incidents": [inc]},
                                       status.CLAUDE_CODE_COMPONENTS)
    assert (got == "Slow Claude Code") is reported


def test_resolved_incident_ignored():
    s = {"components": [], "incidents": [{"name": "x", "status": "resolved",
                                          "components": [{"name": "Claude Code"}]}]}
    assert status.incident_from_summary(s, status.CLAUDE_CODE_COMPONENTS) is None


def test_status_failure_is_silent_and_cached(tmp_path):
    calls = []

    def boom(url):
        calls.append(url)
        raise OSError("offline")

    assert status.current_incident(str(tmp_path), now=100, fetch=boom) is None

    def ok(url):
        calls.append(url)
        return LIVE_SHAPE

    status.current_incident(str(tmp_path), now=200, fetch=ok)
    status.current_incident(str(tmp_path), now=250, fetch=ok)  # cached
    assert len(calls) == 2


# ---------- hook end to end ----------

def run(event, payload, env, now):
    out = io.StringIO()
    assert hook.main(["hook", event], io.StringIO(json.dumps(payload)), out, env, now=now) == 0
    return json.loads(out.getvalue()) if out.getvalue() else None


@pytest.fixture
def env(tmp_path):
    # the local rule; the global model has its own tests (test_turn_model.py)
    return {"CLAUDE_PLUGIN_DATA": str(tmp_path), "CLAUDE_PLUGIN_OPTION_CHECK_STATUS": "false",
            "HOME": str(tmp_path / "home"), "CLAUDE_PLUGIN_OPTION_MODEL": "local"}


def prompt(i, sid="s1"):
    return {"session_id": sid, "prompt_id": "p%d" % i, "prompt": "fix the test", "effort": {"level": "high"},
            "transcript_path": "/nonexistent"}


def test_first_run_says_learning_once_then_nothing(env):
    out = run("prompt", prompt(1), env, 1000)
    assert out["systemMessage"] == \
        MARK + "RainDragon Forecast is learning your pace. Ranges start after 10 turns."
    run("stop", {"session_id": "s1", "prompt_id": "p1"}, env, 1030)
    assert run("prompt", prompt(2), env, 2000) is None  # no repeat in the same session


def test_band_appears_after_enough_turns_and_only_systemmessage(env):
    t = 0
    for i in range(predict.MIN_READY):
        run("prompt", prompt(i), env, t)
        run("stop", {"session_id": "s1", "prompt_id": "p%d" % i}, env, t + 40 + i)
        t += 1000
    out = run("prompt", prompt(99), env, t)
    assert set(out) == {"systemMessage"}
    # under CONFIDENT turns: the wide band, labelled, whatever the setting
    assert out["systemMessage"].startswith(MARK + "Forecast ")
    assert "· 8 in 10 of your similar turns (10, still learning)" in out["systemMessage"]


def test_failure_is_recorded_but_excluded(env):
    run("prompt", prompt(1), env, 0)
    assert run("stop-failure", {"session_id": "s1", "error_type": "overloaded"}, env, 500) is None
    h = store.read_history(env["CLAUDE_PLUGIN_DATA"])
    assert h == [{"t": 500, "dur": 500.0, "pb": "short", "cb": "small", "effort": "high",
                  "ok": False, "incident": False, "error": "overloaded"}]
    assert predict.learned_count(h) == 0


def test_interrupted_turn_is_not_recorded(env):
    run("prompt", prompt(1), env, 0)          # user hits Esc: no Stop
    run("prompt", prompt(2), env, 100)        # next prompt replaces the pending turn
    run("stop", {"session_id": "s1", "prompt_id": "p2"}, env, 130)
    assert [r["dur"] for r in store.read_history(env["CLAUDE_PLUGIN_DATA"])] == [30.0]


def test_stop_for_other_prompt_is_ignored(env):
    run("prompt", prompt(1), env, 0)
    run("stop", {"session_id": "s1", "prompt_id": "other"}, env, 10)
    assert store.read_history(env["CLAUDE_PLUGIN_DATA"]) == []


def test_history_holds_no_prompt_text(env):
    run("prompt", dict(prompt(1), prompt="SECRET customer text"), env, 0)
    run("stop", {"session_id": "s1", "prompt_id": "p1"}, env, 10)
    for root, _, files in os.walk(env["CLAUDE_PLUGIN_DATA"]):
        for f in files:
            assert "SECRET" not in open(os.path.join(root, f)).read()


def test_show_result(env):
    env = dict(env, CLAUDE_PLUGIN_OPTION_SHOW_RESULT="true")
    run("prompt", prompt(1), env, 0)
    out = run("stop", {"session_id": "s1", "prompt_id": "p1"}, env, 75)
    assert out == {"systemMessage": MARK + "done in 1:15"}


@pytest.mark.parametrize("cov", ["50", "80"])
def test_show_result_says_where_the_turn_landed(env, cov):
    env = dict(env, CLAUDE_PLUGIN_OPTION_SHOW_RESULT="true", CLAUDE_PLUGIN_OPTION_BAND=cov)
    t = 0
    for i in range(predict.CONFIDENT):
        run("prompt", prompt(i), env, t)
        run("stop", {"session_id": "s1", "prompt_id": "p%d" % i}, env, t + 40 + i)
        t += 1000
    run("prompt", prompt(99), env, t)
    out = run("stop", {"session_id": "s1", "prompt_id": "p99"}, env, t + 50)
    assert out["systemMessage"] == MARK + "done in 0:50 · inside the forecast"


def test_incident_turn_flagged_and_excluded(env, monkeypatch):
    env = dict(env, CLAUDE_PLUGIN_OPTION_CHECK_STATUS="true")
    monkeypatch.setattr(status, "_fetch", lambda url: {
        "components": [{"name": "Claude Code", "status": "major_outage"}], "incidents": []})
    # the background refresh, run inline here: the FIRST prompt only starts it
    monkeypatch.setattr(status, "_spawn_refresh", lambda d: status.refresh(d, now=0))
    out = run("prompt", prompt(1), env, 0)
    assert "Storm warning" not in out["systemMessage"]
    run("stop", {"session_id": "s1", "prompt_id": "p1"}, env, 30)
    out = run("prompt", prompt(2), env, 40)
    assert MARK + "Storm warning: Claude has a major incident. Expect slower turns." in out["systemMessage"]
    run("stop", {"session_id": "s1", "prompt_id": "p2"}, env, 900)
    h = store.read_history(env["CLAUDE_PLUGIN_DATA"])
    assert [r["incident"] for r in h] == [False, True]
    assert predict.learned_count(h) == 1


def test_garbage_input_never_fails(env):
    out = io.StringIO()
    assert hook.main(["hook", "prompt"], io.StringIO("not json"), out, env) == 0
    assert out.getvalue() == ""


def test_old_session_markers_are_cleaned(env):
    d = env["CLAUDE_PLUGIN_DATA"]
    run("prompt", prompt(1, sid="old"), env, 0)
    old = os.path.join(d, "noted", "old")
    os.utime(old, (0, 0))
    run("prompt", prompt(2, sid="new"), env, 10 * 86400)
    assert not os.path.exists(old) and os.path.exists(os.path.join(d, "noted", "new"))


# ---------- first run, evaluation, kill switch, doctor (M1) ----------

def _turns(env, n, t0=0, dur=lambda i: 40 + i):
    t = t0
    for i in range(n):
        run("prompt", prompt(i), env, t)
        run("stop", {"session_id": "s1", "prompt_id": "p%d" % i}, env, t + dur(i))
        t += 1000
    return t


def test_learning_band_is_wide_then_narrows_at_confident(env):
    h = [rec(10 + i) for i in range(predict.CONFIDENT - 1)]
    b = predict.predict(h, 50, 1000, "high", "50")
    assert b.learning and b.coverage == "80"
    b = predict.predict(h + [rec(30)], 50, 1000, "high", "50")
    assert not b.learning and b.coverage == "50"


def test_ready_band_line_has_no_learning_label(env):
    t = _turns(env, predict.CONFIDENT)
    out = run("prompt", prompt(99), env, t)
    assert out["systemMessage"].startswith(MARK + "Forecast ")
    assert "· half of your similar turns" in out["systemMessage"]
    assert "still learning" not in out["systemMessage"]


def test_evaluate_scores_only_from_the_past():
    h = [rec(10 + (i % 7)) for i in range(60)]
    e = predict.evaluate(h, "50")
    assert e["scored"] == 60 - predict.MIN_READY
    assert 0 <= e["coverage"] <= 1 and e["median_ratio"] >= 1
    assert set(e["by_band"]) == {"80", "50"}
    assert e["by_band"]["80"]["scored"] == predict.CONFIDENT - predict.MIN_READY


def test_evaluate_ignores_failed_and_incident_turns():
    h = [rec(20) for _ in range(40)] + [rec(999, ok=False), rec(999, incident=True)]
    assert predict.evaluate(h)["scored"] == 40 - predict.MIN_READY


def test_evaluate_on_empty_history():
    assert predict.evaluate([])["coverage"] is None


@pytest.mark.parametrize("off", [{"CLAUDE_PLUGIN_OPTION_ENABLED": "false"}, {"RAINDRAGON_FORECAST_OFF": "1"},
                                 {"RAINDRAGON_ETA_OFF": "1"}])
def test_kill_switch_shows_and_records_nothing(env, off):
    env = dict(env, **off)
    assert run("prompt", prompt(1), env, 0) is None
    assert run("stop", {"session_id": "s1", "prompt_id": "p1"}, env, 10) is None
    assert store.read_history(env["CLAUDE_PLUGIN_DATA"]) == []


def test_doctor_reports_counts_never_content(env):
    run("prompt", dict(prompt(1), prompt="SECRET customer text"), env, 0)
    run("stop", {"session_id": "s1", "prompt_id": "p1"}, env, 10)
    out = io.StringIO()
    assert hook.main(["hook", "doctor"], io.StringIO(""), out, env, now=20) == 0
    d = json.loads(out.getvalue())
    assert d["version"] == hook.__version__ and d["turns_recorded"] == 1
    assert d["phase"] == "learning (no band yet)"
    assert d["settings"]["enabled"] is True and d["settings"]["band"] == "50"
    assert "SECRET" not in out.getvalue() and "s1" not in out.getvalue()
    assert d["made_by"] == "RainDragon AI"
    assert d["homepage"].startswith("https://inference.raindragon.ai/?utm_source=raindragon-forecast")


def test_estimate_never_carries_the_maker_link(env):
    _turns(env, 40)
    for i in range(3):
        msg = run("prompt", prompt(100 + i), env, 10**6 + i) or {}
        text = json.dumps(msg)
        assert "raindragon.ai" not in text and "Made by" not in text


def test_eval_command_prints_json(env):
    _turns(env, 15)
    out = io.StringIO()
    assert hook.main(["hook", "eval"], io.StringIO(""), out, env, now=10**6) == 0
    assert json.loads(out.getvalue())["scored"] == 15 - predict.MIN_READY


def test_version_matches_plugin_manifest():
    import pathlib
    root = pathlib.Path(hook.__file__).resolve().parent.parent
    manifest = json.loads((root / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["version"] == hook.__version__


def test_commands_pass_the_data_dir(env, tmp_path):
    other = tmp_path / "elsewhere"
    run("prompt", prompt(1), dict(env, CLAUDE_PLUGIN_DATA=str(other)), 0)
    out = io.StringIO()
    hook.main(["hook", "doctor", "--data", str(other)], io.StringIO(""), out, {}, now=5)
    d = json.loads(out.getvalue())
    assert d["data_dir"] == str(other) and d["turns_in_progress"] == 1


def test_prompt_leaves_pointer_and_live_fields_for_the_mod(env):
    run("prompt", prompt(1), env, 0)
    ptr = os.path.join(env["HOME"], ".raindragon-forecast", "data_dir")
    assert open(ptr).read() == env["CLAUDE_PLUGIN_DATA"]
    p = json.load(open(os.path.join(env["CLAUDE_PLUGIN_DATA"], "pending", "s1")))
    assert p["start"] == 0 and p["learned"] == 0 and p["low"] is None


# ---------- the prompt hook never waits on the network ----------

def test_no_cache_starts_one_background_refresh_and_answers_none(tmp_path):
    spawned = []
    d = str(tmp_path)
    assert status.current_incident(d, now=1000, spawn=spawned.append) is None
    assert status.current_incident(d, now=1010, spawn=spawned.append) is None
    assert spawned == [d]                       # throttled: one per 30 s
    status.current_incident(d, now=1040, spawn=spawned.append)
    assert len(spawned) == 2


def test_stale_cache_is_used_while_it_refreshes(tmp_path):
    d = str(tmp_path)
    down = {"components": [{"name": "Claude Code", "status": "major_outage"}], "incidents": []}
    assert status.refresh(d, now=1000, fetch=lambda url: down)
    spawned = []
    assert status.current_incident(d, now=1300, spawn=spawned.append) == "Claude Code: major outage"
    assert spawned == [d]                       # older than 2 min: refresh started
    assert status.current_incident(d, now=1000 + status.STALE_OK_SECONDS, spawn=spawned.append) is None


def test_prompt_hook_is_fast_even_when_the_network_hangs(env, monkeypatch):
    import time as _time
    env = dict(env, CLAUDE_PLUGIN_OPTION_CHECK_STATUS="true")
    monkeypatch.setattr(status, "_fetch", lambda url: _time.sleep(30))
    monkeypatch.setattr(status, "_spawn_refresh", lambda d: None)
    t = _time.time()
    out = hook.main(["hook", "prompt"], io.StringIO(json.dumps(prompt(1))), io.StringIO(), env)
    assert out == 0 and _time.time() - t < 1.0


def test_refresh_status_command_writes_the_cache(env, monkeypatch):
    monkeypatch.setattr(status, "_fetch", lambda url: {"components": [], "incidents": []})
    hook.main(["hook", "refresh-status", "--data", env["CLAUDE_PLUGIN_DATA"]], io.StringIO(""),
              io.StringIO(), {}, now=50)
    assert os.path.exists(os.path.join(env["CLAUDE_PLUGIN_DATA"], "status_cache.json"))


@pytest.mark.parametrize("cmd", ["/forecast:doctor", "  /forecast:stats", "/raindragon-eta:eval"])
def test_own_commands_are_not_timed(env, cmd):
    _turns(env, predict.MIN_READY)
    before = len(store.read_history(env["CLAUDE_PLUGIN_DATA"]))
    assert run("prompt", dict(prompt(50), prompt=cmd), env, 10**6) is None
    run("stop", {"session_id": "s1", "prompt_id": "p50"}, env, 10**6 + 2)
    assert len(store.read_history(env["CLAUDE_PLUGIN_DATA"])) == before
    assert not os.listdir(os.path.join(env["CLAUDE_PLUGIN_DATA"], "pending"))


def test_other_slash_commands_are_still_timed(env):
    run("prompt", dict(prompt(1), prompt="/review this file"), env, 0)
    run("stop", {"session_id": "s1", "prompt_id": "p1"}, env, 20)
    assert len(store.read_history(env["CLAUDE_PLUGIN_DATA"])) == 1


# ---- rename from RainDragon ETA to RainDragon Forecast ----

def _old_install(home, data):
    """What a RainDragon ETA install leaves behind: its data dir with history,
    and the pointer to it at ~/.raindragon-eta/data_dir."""
    os.makedirs(data)
    with open(os.path.join(data, "history.jsonl"), "w") as f:
        for _ in range(12):
            f.write(json.dumps(rec(40)) + "\n")
    os.makedirs(os.path.join(home, ".raindragon-eta"))
    with open(os.path.join(home, ".raindragon-eta", "data_dir"), "w") as f:
        f.write(data)


def test_old_eta_history_is_copied_into_the_new_plugin(env, tmp_path):
    old = str(tmp_path / "plugins" / "data" / "raindragon-eta-raindragon")
    _old_install(env["HOME"], old)
    out = run("prompt", prompt(1), env, 0)
    # 12 old turns: a range on the very first prompt, no "learning" again
    assert out["systemMessage"].startswith(MARK + "Forecast ")
    assert len(store.read_history(env["CLAUDE_PLUGIN_DATA"])) == 12
    assert len(store.read_history(old)) == 12   # copied, never moved


def test_eta_user_past_learning_is_not_told_has_your_pace(env, tmp_path):
    old = str(tmp_path / "old")
    _old_install(env["HOME"], old)
    with open(os.path.join(old, "history.jsonl"), "a") as f:
        for _ in range(predict.CONFIDENT):
            f.write(json.dumps(rec(40)) + "\n")
    out = run("prompt", prompt(1), env, 0)["systemMessage"]
    assert "has your pace" not in out and "\n" not in out


def test_migration_runs_once_and_never_overwrites(env, tmp_path):
    old = str(tmp_path / "old")
    _old_install(env["HOME"], old)
    _turns(env, 2)                              # first prompt copies 12, then 2 more
    with open(os.path.join(old, "history.jsonl"), "a") as f:
        f.write(json.dumps(rec(99)) + "\n")
    run("prompt", prompt(9), env, 10**6)
    assert len(store.read_history(env["CLAUDE_PLUGIN_DATA"])) == 14


def test_codex_old_folder_and_settings_are_copied(tmp_path):
    home = str(tmp_path / "home")
    old = os.path.join(home, ".codex", "raindragon-eta")
    os.makedirs(old)
    with open(os.path.join(old, "history.jsonl"), "w") as f:
        f.write(json.dumps(rec(40)) + "\n")
    with open(os.path.join(old, "settings.json"), "w") as f:
        json.dump({"mark": "rd", "model": "local"}, f)
    env = {"HOME": home, "CLAUDE_PLUGIN_OPTION_CHECK_STATUS": "false"}
    out = io.StringIO()
    hook.main(["hook", "prompt", "--host", "codex"], io.StringIO(json.dumps(
        {"session_id": "x", "turn_id": "t1", "prompt": "hi"})), out, env, now=0)
    new = os.path.join(home, ".codex", "raindragon-forecast")
    assert len(store.read_history(new)) == 1
    assert json.loads(out.getvalue())["systemMessage"].startswith("RD RainDragon Forecast is learning")


@pytest.mark.parametrize("mark,start", [("symbol", MARK), ("RD", "RD "), ("none", "RainDragon")])
def test_mark_option(env, mark, start):
    out = run("prompt", prompt(1), dict(env, CLAUDE_PLUGIN_OPTION_MARK=mark), 0)
    assert out["systemMessage"].startswith(start)
    p = json.load(open(os.path.join(env["CLAUDE_PLUGIN_DATA"], "pending", "s1")))
    assert p["mark"] == start if mark != "none" else p["mark"] == ""


def test_has_your_pace_is_said_once_ever(env):
    t = _turns(env, predict.CONFIDENT)
    out = run("prompt", prompt(99), env, t)["systemMessage"]
    assert out.split("\n")[1] == MARK + "Forecast has your pace (30 turns). Ranges now show the middle half."
    run("stop", {"session_id": "s1", "prompt_id": "p99"}, env, t + 40)
    out = run("prompt", dict(prompt(100), session_id="s2"), env, t + 10**6)["systemMessage"]
    assert "has your pace" not in out


def _stats(env):
    out = io.StringIO()
    hook.main(["hook", "stats", "--data", env["CLAUDE_PLUGIN_DATA"]], io.StringIO(""), out, {}, now=0)
    return out.getvalue()


def test_stats_screen(env):
    assert _stats(env).startswith(MARK + "RainDragon Forecast is still learning your pace: 0 of 10")
    _turns(env, 40)
    lines = _stats(env).splitlines()
    assert lines[0] == MARK + "RainDragon Forecast · your last 40 turns"
    assert lines[1].startswith("  Inside the range") and lines[1].endswith("of 10 turns (aim 50%)")
    assert lines[2].split() == ["Typical", "turn", "1m"] and lines[3].split() == ["Longest", "turn", "1m"]


# ---------- server comparison (server.py) ----------

def senv(env, mode="shadow", send=True):
    return dict(env, CLAUDE_PLUGIN_OPTION_SERVER_MODE=mode,
                CLAUDE_PLUGIN_OPTION_SERVER_SEND_TEXT="true" if send else "false")


def answer(tok=100):
    return lambda url, body, timeout: {"prediction_id": "etp_" + "a" * 32, "output_tokens": tok,
                                       "model_version": "tinylm-x"}


def seed(env, n, tok=100, dur=50.0):
    d = env["CLAUDE_PLUGIN_DATA"]
    for i in range(n):
        store.append_history(d, dict(rec(dur + i % 5), srv_tok=tok))


def test_nothing_is_sent_without_consent_to_send_text(env):
    posts, spawned = [], []
    for mode in ("shadow", "server", "blend"):
        hook.on_prompt(prompt(1), senv(env, mode, send=False), 0,
                       post=lambda *a: posts.append(a), spawn=lambda *a: spawned.append(a))
    assert posts == [] and spawned == []


def test_shadow_asks_in_the_background_and_shows_only_local(env):
    seed(env, 40)
    local = hook.on_prompt(prompt(1), env, 0)
    spawned = []
    shadow = hook.on_prompt(prompt(2), senv(env), 0, post=answer(),
                            spawn=lambda ev, d, job: spawned.append((ev, job)))
    assert shadow == local                            # the displayed range is the local one
    (ev, job), = spawned
    assert ev == "server-predict" and job["body"]["text"] == "fix the test"
    assert job["body"]["share"] is False and set(job["body"]) == {
        "install_id", "client", "client_version", "model", "text", "share"}


def test_shadow_answer_and_outcome_are_logged_with_the_turn(env):
    e = senv(env)
    hook.on_prompt(prompt(1), e, 1000, spawn=lambda *a: None)
    # what the detached server-predict process leaves behind
    server.write_result(env["CLAUDE_PLUGIN_DATA"], "s1",
                        {"pid": "etp_" + "b" * 32, "tok": 321, "ver": "v", "ms": 80, "start": 1000})
    sent = []
    hook.on_stop({"session_id": "s1", "prompt_id": "p1"}, e, 1042, ok=True,
                 spawn=lambda ev, d, job: sent.append((ev, job)))
    r, = store.read_history(env["CLAUDE_PLUGIN_DATA"])
    assert r["srv_tok"] == 321 and r["dur"] == 42.0 and "shown" not in r
    (ev, job), = sent
    assert ev == "server-outcome" and job["body"]["duration_ms"] == 42000
    assert job["body"]["failed"] is False and job["body"]["prediction_id"] == "etp_" + "b" * 32


def test_a_late_answer_for_another_turn_is_not_attached(env):
    e = senv(env)
    hook.on_prompt(prompt(1), e, 1000, spawn=lambda *a: None)
    server.write_result(env["CLAUDE_PLUGIN_DATA"], "s1", {"pid": "etp_" + "c" * 32, "tok": 5, "start": 1})
    sent = []
    hook.on_stop({"session_id": "s1", "prompt_id": "p1"}, e, 1010, ok=True, spawn=lambda *a: sent.append(a))
    r, = store.read_history(env["CLAUDE_PLUGIN_DATA"])
    assert "srv_tok" not in r and sent == []


@pytest.mark.parametrize("post", [
    lambda u, b, t: (_ for _ in ()).throw(TimeoutError("timed out")),
    lambda u, b, t: (_ for _ in ()).throw(OSError("network unreachable")),
    lambda u, b, t: {"detail": {"code": "model_unavailable"}},          # 503 body / junk
    lambda u, b, t: {"prediction_id": "etp_x", "output_tokens": -3},
])
@pytest.mark.parametrize("mode", ["server", "blend"])
def test_server_and_blend_fall_back_to_local_on_any_failure(env, post, mode):
    seed(env, 40)
    local = hook.on_prompt(prompt(1), env, 0)
    assert hook.on_prompt(prompt(2), senv(env, mode), 0, post=post) == local


def test_server_mode_needs_paired_turns_before_it_shows_anything(env):
    seed(env, predict.MIN_PAIRED - 1)
    store.append_history(env["CLAUDE_PLUGIN_DATA"], rec(50))
    local = hook.on_prompt(prompt(1), env, 0)
    assert hook.on_prompt(prompt(2), senv(env, "server"), 0, post=answer()) == local


def test_server_mode_shows_the_scaled_server_range(env):
    seed(env, 40, tok=100, dur=50.0)                   # 0.5 s per predicted token
    out = hook.on_prompt(prompt(1), senv(env, "server"), 0, post=answer(tok=400))
    p = store.pop_pending(env["CLAUDE_PLUGIN_DATA"], "s1")
    assert p["shown"] == "server" and 190 < p["low"] <= p["high"] < 220   # 400 x 0.5 s, spread 50-54 s
    assert p["loc"] is not None and p["loc"][1] < 60                       # the local range is kept too
    assert out.startswith(MARK + "Forecast ")


def test_server_band_and_blend_math():
    paired = [dict(rec(d), srv_tok=10) for d in (10, 20, 40, 80)] * 10
    b = predict.server_band(paired, 20, "50")
    assert b.group == "server" and b.low < 2 * 25 < b.high       # scale 2.5-ish s/token x 20
    lb = predict.Band(4.0, 16.0, 40, "all", "50")
    m = predict.blend(lb, predict.Band(16.0, 64.0, 40, "server", "50"))
    assert (m.low, m.high) == (8.0, 32.0)
    assert predict.blend(lb, None) is lb


def test_eval_compares_local_and_server_on_paired_turns(env):
    d = env["CLAUDE_PLUGIN_DATA"]
    for i in range(30):
        store.append_history(d, dict(rec(40 + i), srv_tok=100, loc=[30.0, 60.0]))
    out = io.StringIO()
    hook.main(["hook", "eval", "--data", d], io.StringIO(""), out, {}, now=0)
    j = json.loads(out.getvalue())["server"]
    assert j["paired_turns"] == 30 and j["local"]["scored"] == 30
    assert j["server"]["scored"] == 30 - predict.MIN_PAIRED


def test_shadow_never_writes_the_prompt_to_disk(env):
    hook.on_prompt(dict(prompt(1), prompt="SECRET customer text"), senv(env), 0, spawn=lambda *a: None)
    hook.on_stop({"session_id": "s1", "prompt_id": "p1"}, senv(env), 10, ok=True, spawn=lambda *a: None)
    for root, _, files in os.walk(env["CLAUDE_PLUGIN_DATA"]):
        for f in files:
            assert "SECRET" not in open(os.path.join(root, f)).read()


def test_server_predict_command_writes_numbers_only(env, monkeypatch):
    d = env["CLAUDE_PLUGIN_DATA"]
    monkeypatch.setattr(server, "_post", answer(tok=77))
    job = {"sid": "s1", "start": 5, "url": "https://example.invalid",
           "body": server.predict_body("iid-12345678", "0", "SECRET")}
    hook.main(["hook", "server-predict", "--data", d], io.StringIO(json.dumps(job)), io.StringIO(), {})
    res = server.pop_result(d, "s1")
    assert res["tok"] == 77 and res["start"] == 5 and "SECRET" not in json.dumps(res)


def test_doctor_reports_server_settings(env):
    j = hook.doctor(senv(env, "blend"), 0)
    assert j["server"]["mode"] == "blend" and j["server"]["active"] is True
    assert hook.doctor(env, 0)["server"] == {"mode": "shadow", "send_text": False, "active": False,
                                             "url": server.DEFAULT_URL, "turns_with_answer": 0,
                                             "turns_with_error": 0}
