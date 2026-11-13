"""Codex CLI and Gemini CLI hosts. Every payload and rollout line here is
synthetic, shaped after the hook docs (learn.chatgpt.com/docs/hooks,
geminicli.com/docs/hooks/reference) and the CLIs' source; none comes from a
real session."""

import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts"))

import raindragon_eta_hook as hook  # noqa: E402
from raindragon_eta import hosts, predict, status, store  # noqa: E402

MARK = "\u260a\u260b "  # the Dragon's Head and Tail mark that starts every line

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "cli")


def fixture(name):
    with open(os.path.join(FIX, name)) as f:
        return json.load(f)


def run(event, payload, env, now, host):
    out = io.StringIO()
    argv = ["hook", event, "--host", host]
    assert hook.main(argv, io.StringIO(json.dumps(payload)), out, env, now=now) == 0
    text = out.getvalue()
    if not text:
        return None
    parsed = json.loads(text)   # stdout is ONE JSON object or nothing
    assert set(parsed) == {"systemMessage"}
    return parsed


@pytest.fixture(autouse=True)
def local_rule(monkeypatch):
    """These tests pin the local rule; the global model has its own tests."""
    monkeypatch.setattr(hook, "DEFAULT_MODEL", "local")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Status checks default on; never let a test start a real fetch."""
    monkeypatch.setattr(status, "_spawn_refresh", lambda *a, **k: None)
    monkeypatch.setattr(status, "_fetch", lambda url: (_ for _ in ()).throw(OSError("offline")))


@pytest.fixture
def env(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    return {"HOME": str(home)}


def codex_dir(env):
    return os.path.join(env["HOME"], ".codex", "raindragon-forecast")


def gemini_dir(env):
    return os.path.join(env["HOME"], ".gemini", "raindragon-forecast")


# ---------- payload builders (from the synthetic fixtures) ----------

def codex_prompt(i, sid="cx-session-1", transcript=None, model="gpt-5.5-codex"):
    p = dict(fixture("codex_user_prompt_submit.json"), session_id=sid, turn_id="turn-%d" % i, model=model)
    p["transcript_path"] = transcript
    return p


def codex_stop(i, sid="cx-session-1"):
    return dict(fixture("codex_stop.json"), session_id=sid, turn_id="turn-%d" % i)


def gemini_before(sid="gm-session-1"):
    return dict(fixture("gemini_before_agent.json"), session_id=sid)


def gemini_after(sid="gm-session-1", response="Done: renamed the helper and updated two call sites."):
    return dict(fixture("gemini_after_agent.json"), session_id=sid, prompt_response=response)


def codex_turns(env, n, t0=0, dur=lambda i: 40 + i):
    t = t0
    for i in range(n):
        run("prompt", codex_prompt(1000 + t0 + i), env, t, "codex")
        run("stop", codex_stop(1000 + t0 + i), env, t + dur(i), "codex")
        t += dur(i) + 5
    return t


def gemini_turns(env, n, t0=0, dur=lambda i: 20 + i):
    t = t0
    for i in range(n):
        run("prompt", gemini_before(), env, t, "gemini")
        run("stop", gemini_after(), env, t + dur(i), "gemini")
        t += dur(i) + 5
    return t


# ---------- Codex ----------

def test_codex_start_stop_pairing_records_one_turn(env):
    assert "learning your pace" in run("prompt", codex_prompt(1), env, 100, "codex")["systemMessage"]
    assert run("stop", codex_stop(1), env, 142.5, "codex") is None
    h = store.read_history(codex_dir(env))
    assert len(h) == 1 and h[0]["dur"] == 42.5 and h[0]["ok"] is True
    assert h[0]["effort"] == "gpt-5.5-codex"   # the model is the "similar" label


def test_codex_stop_for_another_turn_is_ignored(env):
    run("prompt", codex_prompt(1), env, 0, "codex")
    run("stop", codex_stop(2), env, 30, "codex")
    assert store.read_history(codex_dir(env)) == []


def test_codex_interrupt_drops_the_turn(env):
    run("prompt", codex_prompt(1), env, 0, "codex")
    assert run("interrupt", dict(fixture("codex_interrupt.json"), turn_id="turn-1"), env, 10, "codex") is None
    run("stop", codex_stop(1), env, 30, "codex")      # a late Stop finds nothing
    assert store.read_history(codex_dir(env)) == []


def test_codex_first_run_learns_from_zero_then_wide_then_chosen_band(env):
    out = run("prompt", codex_prompt(1), env, 0, "codex")["systemMessage"]
    assert out == "%sRainDragon Forecast is learning your pace. Ranges start after 10 turns." % MARK
    run("stop", codex_stop(1), env, 40, "codex")
    t = codex_turns(env, predict.MIN_READY - 1, t0=100)
    out = run("prompt", codex_prompt(9000), env, t, "codex")["systemMessage"]
    assert "8 in 10" in out and "still learning" in out
    run("stop", codex_stop(9000), env, t + 40, "codex")
    t = codex_turns(env, predict.CONFIDENT, t0=t + 100)
    out = run("prompt", codex_prompt(9001), env, t, "codex")["systemMessage"]
    assert out.startswith(MARK + "Forecast ") and "· half of your similar turns" in out
    assert "still learning" not in out


def _write_rollout(tmp_path, turn_id, payload_type="task_complete", error=None, duration_ms=73_000):
    """A synthetic rollout: session_meta, turn_context, then the turn's end."""
    path = tmp_path / "rollout-2026-10-19T10-00-00-synthetic.jsonl"
    end = {"type": payload_type, "turn_id": turn_id, "duration_ms": duration_ms,
           "started_at": 1, "completed_at": 74}
    if payload_type == "task_complete":
        end["last_agent_message"] = None
        end["time_to_first_token_ms"] = 900
        if error is not None:
            end["error"] = error
    else:
        end["reason"] = "interrupted"
    lines = [
        {"timestamp": "2026-10-19T10:00:00.000Z", "type": "session_meta", "payload": {"id": "synthetic"}},
        {"timestamp": "2026-10-19T10:00:01.000Z", "type": "turn_context",
         "payload": {"turn_id": turn_id, "model": "gpt-5.5-codex", "effort": "high"}},
        {"timestamp": "2026-10-19T10:00:02.000Z", "type": "event_msg",
         "payload": {"type": "task_started", "turn_id": turn_id}},
        {"timestamp": "2026-10-19T10:01:15.000Z", "type": "event_msg", "payload": end},
    ]
    path.write_text("".join(json.dumps(x) + "\n" for x in lines))
    return str(path)


@pytest.mark.parametrize("error,want", [
    ({"message": "synthetic overload", "codex_error_info": "server_overloaded"}, "server_overloaded"),
    ({"message": "synthetic", "codex_error_info": {"http_connection_failed": {"http_status_code": 502}}},
     "http_connection_failed"),
    ({"message": "synthetic"}, "error"),
])
def test_codex_errored_turn_is_classified_failed_from_the_rollout(env, tmp_path, error, want):
    # Codex runs no Stop hook when a turn errors, so the next prompt settles it
    rollout = _write_rollout(tmp_path, "turn-1", error=error)
    run("prompt", codex_prompt(1, transcript=rollout), env, 0, "codex")
    run("prompt", codex_prompt(2, transcript=rollout), env, 200, "codex")
    h = store.read_history(codex_dir(env))
    assert len(h) == 1
    assert h[0]["ok"] is False and h[0]["error"] == want and h[0]["dur"] == 73.0
    assert "synthetic" not in json.dumps(h)          # never the error message text
    assert predict.learned_count(h) == 0


def test_codex_aborted_or_clean_unpaired_turn_is_dropped(env, tmp_path):
    rollout = _write_rollout(tmp_path, "turn-1", payload_type="turn_aborted")
    run("prompt", codex_prompt(1, transcript=rollout), env, 0, "codex")
    run("prompt", codex_prompt(2, transcript=rollout), env, 200, "codex")
    rollout2 = _write_rollout(tmp_path, "turn-2")    # completed without error, but no Stop seen
    run("prompt", codex_prompt(3, transcript=rollout2), env, 400, "codex")
    assert store.read_history(codex_dir(env)) == []


def test_codex_missing_or_unreadable_rollout_is_harmless(env, tmp_path):
    run("prompt", codex_prompt(1, transcript=str(tmp_path / "gone.jsonl")), env, 0, "codex")
    run("prompt", codex_prompt(2, transcript=str(tmp_path / "x.jsonl.zst")), env, 10, "codex")
    run("stop", codex_stop(2), env, 50, "codex")
    h = store.read_history(codex_dir(env))
    assert len(h) == 1 and h[0]["ok"]


def test_codex_outcome_reader_keeps_the_last_record_for_the_turn(tmp_path):
    rollout = _write_rollout(tmp_path, "turn-7", error={"codex_error_info": "usage_limit_exceeded"})
    assert hosts.codex_turn_outcome(rollout, "turn-7") == {
        "kind": "complete", "duration_ms": 73_000, "error": "usage_limit_exceeded"}
    assert hosts.codex_turn_outcome(rollout, "turn-8") is None
    assert hosts.codex_turn_outcome(None, "turn-7") is None


def test_codex_history_holds_no_prompt_text_or_paths(env, tmp_path):
    rollout = _write_rollout(tmp_path, "turn-1")
    run("prompt", codex_prompt(1, transcript=rollout), env, 0, "codex")
    run("stop", codex_stop(1), env, 30, "codex")
    raw = open(os.path.join(codex_dir(env), "history.jsonl")).read()
    p = codex_prompt(1)["prompt"]
    assert p not in raw and "rollout" not in raw and "cx-session" not in raw


def test_codex_settings_file_and_no_inherited_claude_options(env):
    os.makedirs(codex_dir(env))
    with open(os.path.join(codex_dir(env), "settings.json"), "w") as f:
        json.dump({"show_result": True}, f)
    env = dict(env, CLAUDE_PLUGIN_OPTION_ENABLED="false", CLAUDE_PLUGIN_DATA="/should/not/be/used")
    run("prompt", codex_prompt(1), env, 0, "codex")
    assert run("stop", codex_stop(1), env, 30, "codex")["systemMessage"] == MARK + "done in 0:30"


def test_codex_kill_switch(env):
    env = dict(env, RAINDRAGON_FORECAST_OFF="1")
    assert run("prompt", codex_prompt(1), env, 0, "codex") is None
    run("stop", codex_stop(1), env, 30, "codex")
    assert not os.path.exists(os.path.join(codex_dir(env), "history.jsonl"))


def test_codex_has_no_status_source(env, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("no network for codex")
    monkeypatch.setattr(status, "_fetch", boom)
    monkeypatch.setattr(status, "_spawn_refresh", boom)
    run("prompt", codex_prompt(1), env, 0, "codex")
    out = io.StringIO()
    assert hook.main(["hook", "refresh-status", "--host", "codex"], io.StringIO(""), out, env, now=0) == 0


# ---------- Gemini ----------

def test_gemini_start_stop_pairing(env):
    assert "learning your pace" in run("prompt", gemini_before(), env, 10, "gemini")["systemMessage"]
    run("stop", gemini_after(sid="gm-other"), env, 20, "gemini")          # other session: ignored
    assert store.read_history(gemini_dir(env)) == []
    run("stop", gemini_after(), env, 35, "gemini")
    h = store.read_history(gemini_dir(env))
    assert len(h) == 1 and h[0]["dur"] == 25 and h[0]["ok"] and h[0]["effort"] is None


@pytest.mark.parametrize("response", ["", "   ", "[no response text]", None])
def test_gemini_empty_reply_is_failed_and_excluded(env, response):
    run("prompt", gemini_before(), env, 0, "gemini")
    run("stop", gemini_after(response=response), env, 5, "gemini")
    h = store.read_history(gemini_dir(env))
    assert h[0]["ok"] is False and h[0]["error"] == "empty_response"
    assert predict.learned_count(h) == 0


def test_gemini_unfinished_turn_is_dropped_at_next_prompt(env):
    # an error or Ctrl+C fires no AfterAgent: the next BeforeAgent replaces it
    run("prompt", gemini_before(), env, 0, "gemini")
    run("prompt", gemini_before(), env, 500, "gemini")
    run("stop", gemini_after(), env, 520, "gemini")
    h = store.read_history(gemini_dir(env))
    assert [r["dur"] for r in h] == [20]


def test_gemini_first_run_learns_from_zero(env):
    out = run("prompt", gemini_before(), env, 0, "gemini")["systemMessage"]
    assert "learning your pace" in out
    run("stop", gemini_after(), env, 20, "gemini")
    t = gemini_turns(env, predict.MIN_READY - 2, t0=100)
    assert run("prompt", gemini_before(), env, t, "gemini") is None   # said once per session
    run("stop", gemini_after(), env, t + 20, "gemini")
    out = run("prompt", gemini_before(), env, t + 100, "gemini")["systemMessage"]
    assert "8 in 10" in out and "still learning" in out


def test_gemini_reply_text_is_never_stored(env):
    run("prompt", gemini_before(), env, 0, "gemini")
    run("stop", gemini_after(response="SYNTHETIC-REPLY-MARKER"), env, 9, "gemini")
    blob = ""
    for root, _, files in os.walk(gemini_dir(env)):
        for n in files:
            blob += open(os.path.join(root, n)).read()
    assert "SYNTHETIC-REPLY-MARKER" not in blob and gemini_before()["prompt"] not in blob


# ---------- separate histories ----------

def test_each_host_keeps_its_own_history(env, tmp_path):
    claude_env = dict(env, CLAUDE_PLUGIN_DATA=str(tmp_path / "claude-data"),
                      CLAUDE_PLUGIN_OPTION_CHECK_STATUS="false")
    codex_turns(env, 3, dur=lambda i: 100)
    gemini_turns(env, 2, dur=lambda i: 7)
    out = io.StringIO()
    hook.main(["hook", "prompt"], io.StringIO(json.dumps(
        {"session_id": "c1", "prompt_id": "p1", "prompt": "synthetic", "effort": {"level": "high"}})),
        out, claude_env, now=0)
    hook.main(["hook", "stop"], io.StringIO(json.dumps({"session_id": "c1", "prompt_id": "p1"})),
              io.StringIO(), claude_env, now=60)
    assert [r["dur"] for r in store.read_history(codex_dir(env))] == [100] * 3
    assert [r["dur"] for r in store.read_history(gemini_dir(env))] == [7] * 2
    assert [r["dur"] for r in store.read_history(str(tmp_path / "claude-data"))] == [60]
    # only Claude Code's live status line needs the pointer file
    pointer = os.path.join(env["HOME"], ".raindragon-forecast", "data_dir")
    assert open(pointer).read() == str(tmp_path / "claude-data")


def test_codex_band_never_counts_gemini_turns(env):
    gemini_turns(env, 40, dur=lambda i: 5)
    out = run("prompt", codex_prompt(1), env, 99999, "codex")["systemMessage"]
    assert "learning your pace" in out


def test_doctor_names_the_host(env):
    codex_turns(env, 2)
    out = io.StringIO()
    hook.main(["hook", "doctor", "--host", "codex"], io.StringIO(""), out, env, now=0)
    d = json.loads(out.getvalue())
    assert d["host"] == "codex" and d["turns_recorded"] == 2 and d["data_dir"] == codex_dir(env)


def test_unknown_host_does_nothing(env):
    assert run("prompt", codex_prompt(1), env, 0, "cursor") is None
    assert not os.path.exists(os.path.join(env["HOME"], ".raindragon-forecast"))


# ---------- Gemini status (Google Workspace dashboard) ----------

GEMINI = {"title": "Gemini", "id": status.GEMINI_PRODUCT_ID}
OTHER = {"title": "Gmail", "id": "synthetic-gmail-id"}


def gi(end=None, severity="high", impact="SERVICE_OUTAGE", products=(GEMINI,)):
    return {"begin": "2026-10-19T09:00:00+00:00", "end": end, "severity": severity,
            "status_impact": impact, "affected_products": list(products),
            "external_desc": "synthetic text"}


@pytest.mark.parametrize("inc,reported", [
    (gi(), True),
    (gi(severity="medium"), True),                               # outage impact alone counts
    (gi(impact="SERVICE_DISRUPTION"), True),                     # high severity alone counts
    (gi(severity="medium", impact="SERVICE_DISRUPTION"), False),
    (gi(severity="low", impact="SERVICE_INFORMATION"), False),
    (gi(end="2026-10-19T10:00:00+00:00"), False),                # resolved
    (gi(products=(OTHER,)), False),                              # another product
])
def test_gemini_status_is_major_only(inc, reported):
    assert (status.incident_from_google([inc]) is not None) is reported


def test_gemini_status_cache_is_trimmed_and_turn_flagged(env, monkeypatch):
    os.makedirs(gemini_dir(env))
    with open(os.path.join(gemini_dir(env), "settings.json"), "w") as f:
        json.dump({"check_status": True}, f)
    feed = [gi(products=(OTHER,))] * 50 + [gi()]
    seen = []

    def fetch(url):
        seen.append(url)
        return feed
    monkeypatch.setattr(status, "_fetch", fetch)
    monkeypatch.setattr(status, "_spawn_refresh", lambda d, host="claude": status.refresh(
        d, now=0, url=status.SOURCES[host][0]))
    run("prompt", gemini_before(), env, 0, "gemini")                # first prompt starts the refresh
    run("stop", gemini_after(), env, 10, "gemini")
    out = run("prompt", gemini_before(), env, 20, "gemini")["systemMessage"]
    assert "Gemini outage" in out
    assert seen == [status.GOOGLE_INCIDENTS_URL]
    cached = json.load(open(os.path.join(gemini_dir(env), "status_cache.json")))["summary"]
    assert len(cached) == 1 and "external_desc" not in cached[0]
    run("stop", gemini_after(), env, 900, "gemini")
    h = store.read_history(gemini_dir(env))
    assert [r["incident"] for r in h] == [False, True]


def test_gemini_status_off_by_setting(env, monkeypatch):
    os.makedirs(gemini_dir(env))
    with open(os.path.join(gemini_dir(env), "settings.json"), "w") as f:
        json.dump({"check_status": False}, f)

    def boom(*a, **k):
        raise AssertionError("status check is off")
    monkeypatch.setattr(status, "_spawn_refresh", boom)
    run("prompt", gemini_before(), env, 0, "gemini")


# ---------- install docs match what the code reads ----------

def test_install_examples_use_events_the_code_handles():
    doc = open(os.path.join(os.path.dirname(__file__), "..", "docs", "INSTALL_CLI.md")).read()
    for needle in ("UserPromptSubmit", "Stop", "Interrupt", "prompt --host codex", "stop --host codex",
                   "interrupt --host codex", "BeforeAgent", "AfterAgent", "prompt --host gemini",
                   "stop --host gemini", "/hooks", "enableHooks"):
        assert needle in doc, needle
