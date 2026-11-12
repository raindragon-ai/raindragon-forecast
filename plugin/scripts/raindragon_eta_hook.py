#!/usr/bin/env python3
"""RainDragon Forecast hook entry point: <event> [--host claude|codex|gemini].

Claude Code (default host; plugin hooks/hooks.json):

UserPromptSubmit  start the clock, show the band for this turn
Stop              stop the clock, save the turn to local history
StopFailure       the turn failed: save it as failed so it never enters a band

Codex CLI (--host codex; ~/.codex/hooks.json, docs/INSTALL_CLI.md):
  prompt     UserPromptSubmit. Before starting the clock, a previous turn of
             the session whose Stop never came is looked up in Codex's
             rollout log: a task_complete with an error is saved as failed.
  stop       Stop
  interrupt  Interrupt: the user stopped the turn; dropped, never recorded

Gemini CLI (--host gemini; ~/.gemini/settings.json):
  prompt     BeforeAgent
  stop       AfterAgent; an empty reply is saved as failed

Each host keeps its own history in its own folder (hosts.py).

Also run by hand (or via the plugin's slash commands):
  doctor            version, settings and counts -- never content -- to paste
                    into a bug report
  refresh-status    fetch status.claude.com into the cache; started detached
                    by the prompt hook, which never waits on the network
  stats             plain-text summary for /forecast:stats: how often the
                    range held, typical and longest turn
  eval              the same measurement as JSON (coverage and band width),
                    replayed in order; with server answers in history, also
                    local vs server vs blend on those turns
  server-predict    started detached by the prompt hook in server shadow mode
  server-outcome    started detached by the Stop hook: the real duration
                    (server.py; both get their payload on stdin)

Kill switch: the plugin option "enabled" (in /config) or RAINDRAGON_FORECAST_OFF=1
(the old RAINDRAGON_ETA_OFF=1 still works).
Off means nothing is shown and nothing is recorded.

Contract with Claude Code:
  * stdout is either empty or ONE JSON object with "systemMessage". Plain
    stdout from UserPromptSubmit would be added to the model's context, and
    the estimate is for the user, not the model.
  * always exit 0. A timing aid must never block or break a turn, so every
    error is swallowed.
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from raindragon_eta import __version__, fmt, hosts, predict, server, status, store  # noqa: E402

NAME = "RainDragon Forecast"
#: The mark that starts every line: the Dragon's Head and Tail symbols. The
#: "mark" option swaps it for plain letters where a terminal font shows boxes.
MARKS = {"symbol": "\u260a\u260b ", "rd": "RD ", "none": ""}
#: Who makes it. Named, with a link, in doctor output, the extension options page
#: and the README only -- never inside an estimate (CLAIMS.md, "Made by").
MADE_BY = "RainDragon AI"
HOMEPAGE = "https://inference.raindragon.ai/?utm_source=raindragon-forecast&utm_medium=doctor"
MAX_TURN_SECONDS = 6 * 3600   # longer than this is a turn left open, not a duration


def _flag(env: dict, key: str, default: bool) -> bool:
    v = env.get("CLAUDE_PLUGIN_OPTION_" + key)
    if v is None or v == "":
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def enabled(env: dict) -> bool:
    for key in ("RAINDRAGON_FORECAST_OFF", "RAINDRAGON_ETA_OFF"):   # old name still works
        if str(env.get(key) or "").strip().lower() in ("1", "true", "yes", "on"):
            return False
    return _flag(env, "ENABLED", True)


def mark(env: dict) -> str:
    return MARKS.get(str(env.get("CLAUDE_PLUGIN_OPTION_MARK") or "symbol").strip().lower(),
                     MARKS["symbol"])


def _transcript_bytes(path) -> int:
    try:
        return os.path.getsize(path) if path else 0
    except OSError:
        return 0


def _settings(env: dict) -> dict:
    return {"enabled": enabled(env), "band": env.get("CLAUDE_PLUGIN_OPTION_BAND") or "50",
            "show_result": _flag(env, "SHOW_RESULT", False),
            "check_status": _flag(env, "CHECK_STATUS", True),
            "mark": env.get("CLAUDE_PLUGIN_OPTION_MARK") or "symbol"}


#: Our own slash commands are not turns worth timing. The old prefix stays
#: for anyone still on a RainDragon ETA install alongside.
OWN_COMMANDS = ("/forecast:", "/raindragon-eta:")


#: What a status warning names, per host.
INCIDENT_TEXT = {"claude": "Storm warning: Claude has a major incident. Expect slower turns.",
                 "gemini": "Storm warning: Google reports a Gemini outage. Expect slower turns."}


def _settle_unfinished(d: str, sid: str, p: dict, host: str) -> None:
    """A turn whose end hook never came. Codex skips Stop when a turn ends in
    an error, and writes the outcome to its rollout log: record that as
    failed so doctor counts it. Anything else (aborted, unknown) is dropped,
    as before."""
    if host != "codex":
        return
    o = hosts.codex_turn_outcome(p.get("transcript_path"), p.get("prompt_id"))
    if not o or o["kind"] != "complete" or not o["error"]:
        return
    dur = (o["duration_ms"] or 0) / 1000.0
    rec = {"t": int(float(p.get("start", 0)) + dur), "dur": round(dur, 2), "pb": p.get("pb"),
           "cb": p.get("cb"), "effort": p.get("effort"), "ok": False,
           "incident": bool(p.get("incident")), "error": o["error"]}
    store.append_history(d, rec)


def _server_side(d: str, sid: str, prompt_text: str, history: list, band, coverage: str,
                 env: dict, now: float, post=None, spawn=None):
    """(band to show, which one it is). Never raises; local on any failure."""
    cfg = server.settings(env)
    sidf = store._safe(sid)
    server.pop_result(d, sidf)            # a late answer for an earlier turn
    if not server.active(cfg) or not prompt_text.strip():
        return band, "local"
    body = server.predict_body(server.install_id(d), __version__, prompt_text)
    if cfg["mode"] == "shadow":
        try:
            (spawn or server.spawn)("server-predict", d,
                                    {"sid": sidf, "start": now, "url": cfg["url"], "body": body})
        except Exception:  # noqa: BLE001
            pass
        return band, "local"
    res = server.ask(cfg, body, server.PROMPT_TIMEOUT_S, post)
    server.write_result(d, sidf, dict(res, start=now))
    srv = predict.server_band(history, res["tok"], coverage) if "tok" in res else None
    if not srv:
        return band, "local"
    if cfg["mode"] == "server":
        return srv, "server"
    return (predict.blend(band, srv), "blend") if band else (band, "local")


def on_prompt(inp: dict, env: dict, now: float, fetch=None, host: str = "claude",
              post=None, spawn=None) -> str:
    d = store.data_dir(env)
    store.cleanup(d, now)
    if host != "claude":
        old = store.pop_pending(d, inp.get("session_id") or "")
        if old:
            _settle_unfinished(d, inp.get("session_id") or "", old, host)
    if (inp.get("prompt") or "").lstrip().startswith(OWN_COMMANDS):
        # /forecast:doctor and :stats take a second and say nothing about
        # the user's pace: no estimate, and no pending turn, so Stop records
        # nothing and the status-line clock does not start.
        store.pop_pending(d, inp.get("session_id") or "")
        return ""
    store.write_json(d, "settings_seen.json", dict(_settings(env), at=int(now)))
    if host == "claude":
        store.write_pointer(env, d)   # for the Claude Code live status line only
    sid = inp.get("session_id") or ""
    effort = hosts.similar_label(inp, host)
    pc = len(inp.get("prompt") or "")
    tb = _transcript_bytes(inp.get("transcript_path"))
    coverage = env.get("CLAUDE_PLUGIN_OPTION_BAND") or "50"
    if coverage not in predict.BANDS:
        coverage = "50"

    incident = None
    if _flag(env, "CHECK_STATUS", True):
        kw = {"fetch": fetch} if fetch else {}
        if host in status.SOURCES:
            incident = status.current_incident(d, status.CLAUDE_CODE_COMPONENTS, now=now,
                                               host=host, **kw)

    history = store.read_history(d)
    local = predict.predict(history, pc, tb, effort, coverage)
    band, shown = local, "local"
    if host == "claude":
        band, shown = _server_side(d, sid, inp.get("prompt") or "", history, local, coverage,
                                   env, now, post, spawn)

    pending = {
        "prompt_id": hosts.turn_key(inp, host), "start": now,
        "pb": predict.prompt_bucket(pc), "cb": predict.context_bucket(tb),
        "effort": effort, "incident": bool(incident), "cov": band.coverage if band else coverage,
        "low": band.low if band else None, "high": band.high if band else None,
        "n": band.n if band else None, "learning": bool(band.learning) if band else None,
        "learned": predict.learned_count(history),
        "mark": mark(env),   # for the live status line, which gets no plugin options
        "loc": [round(local.low, 2), round(local.high, 2)] if local else None,
        "shown": shown,
    }
    if host == "codex" and inp.get("transcript_path"):
        # kept only to look up this turn's outcome if its Stop never comes
        pending["transcript_path"] = str(inp["transcript_path"])
    store.write_pending(d, sid, pending)

    if not band or band.learning:
        if store.read_json(d, "learned.json") is None:
            store.write_json(d, "learned.json", {"said": False})
    lines = []
    if band:
        lines.append(fmt.band_line(band.low, band.high, band.n, band.coverage, band.learning))
        if not band.learning and store.read_json(d, "learned.json") == {"said": False}:
            # once ever, and only to someone who saw the learning phase (not a
            # RainDragon ETA user whose copied history is already past it).
            # Kept outside noted/, which is cleaned after 2 days.
            store.write_json(d, "learned.json", {"said": True, "at": int(now)})
            lines.append("Forecast has your pace (%d turns)." % predict.CONFIDENT
                         + (" Ranges now show the middle half." if band.coverage == "50" else ""))
    elif store.mark_noted(d, sid):
        lines.append("%s is learning your pace. Ranges start after %d turns."
                     % (NAME, predict.MIN_READY))
    if incident:
        lines.append(INCIDENT_TEXT.get(host, INCIDENT_TEXT["claude"]))
    return "\n".join(mark(env) + line for line in lines)


def _attach_server(d: str, sid: str, p: dict, rec: dict, dur: float, ok: bool, env: dict,
                   spawn=None) -> None:
    """Copy this turn's server answer (numbers only) into the history record and
    send the real duration back. Answers for another turn are ignored."""
    res = server.pop_result(d, store._safe(sid))
    if p.get("loc"):
        rec["loc"] = p["loc"]
    if p.get("shown") and p["shown"] != "local":
        rec["shown"] = p["shown"]
    if not res or res.get("start") != p.get("start"):
        return
    if "tok" in res:
        rec.update(srv_tok=res["tok"], srv_ms=res.get("ms"), srv_ver=res.get("ver"))
        cfg = server.settings(env)
        if server.active(cfg) and res.get("pid"):
            try:
                (spawn or server.spawn)("server-outcome", d, {"url": cfg["url"], "body": server.outcome_body(
                    server.install_id(d), res["pid"], dur, ok)})
            except Exception:  # noqa: BLE001
                pass
    elif res.get("err"):
        rec["srv_err"] = str(res["err"])[:20]


def on_stop(inp: dict, env: dict, now: float, ok: bool, host: str = "claude",
            error: str = None, spawn=None) -> str:
    d = store.data_dir(env)
    p = store.pop_pending(d, inp.get("session_id") or "")
    if not p:
        return ""
    key = hosts.turn_key(inp, host)
    if p.get("prompt_id") and key and p["prompt_id"] != key:
        return ""
    dur = now - float(p.get("start", now))
    if dur <= 0 or dur > MAX_TURN_SECONDS:
        return ""
    rec = {"t": int(now), "dur": round(dur, 2), "pb": p.get("pb"), "cb": p.get("cb"),
           "effort": p.get("effort"), "ok": ok, "incident": bool(p.get("incident"))}
    if not ok:
        rec["error"] = error or inp.get("error_type") or "unknown"
    if host == "claude":
        _attach_server(d, inp.get("session_id") or "", p, rec, dur, ok, env, spawn)
    store.append_history(d, rec)
    if ok and _flag(env, "SHOW_RESULT", False):
        return mark(env) + fmt.done_line(dur, p.get("low"), p.get("high"))
    return ""


def doctor(env: dict, now: float, host: str = "claude") -> dict:
    """State for a bug report. Counts and settings only: no prompt text,
    no file names, no session ids."""
    d = store.data_dir(env)
    h = store.read_history(d)
    try:
        pending = len([n for n in os.listdir(os.path.join(d, "pending")) if not n.endswith(".tmp")])
    except OSError:
        pending = 0
    cache_age = None
    try:
        with open(os.path.join(d, "status_cache.json")) as f:
            cache_age = round(now - float(json.load(f).get("fetched_at", 0)))
    except (OSError, ValueError):
        pass
    good = predict.learned_count(h)
    # Slash commands do not get the plugin options, so report the settings the
    # last prompt hook saw (None before the first prompt).
    seen = store.read_json(d, "settings_seen.json")
    cfg = server.settings(env)
    return {
        "server": {"mode": cfg["mode"], "send_text": cfg["send_text"], "active": server.active(cfg),
                   "url": cfg["url"], "turns_with_answer": sum(1 for r in h if r.get("srv_tok")),
                   "turns_with_error": sum(1 for r in h if r.get("srv_err"))},
        "name": "raindragon-forecast", "version": __version__, "host": host,
        "settings": seen,
        "data_dir": d,
        "turns_recorded": len(h),
        "turns_used": good,
        "turns_failed": sum(1 for r in h if not r.get("ok")),
        "turns_in_incident": sum(1 for r in h if r.get("ok") and r.get("incident")),
        "phase": ("learning (no band yet)" if good < predict.MIN_READY
                  else "learning (wide band)" if good < predict.CONFIDENT else "ready"),
        "turns_in_progress": pending,
        "status_cache_age_s": cache_age,
        "python": sys.version.split()[0],
        "made_by": MADE_BY,
        "homepage": HOMEPAGE,
    }


def stats_text(history: list, coverage: str, mk: str) -> str:
    """The /forecast:stats screen. Plain text, so Claude can show it as is."""
    good = predict.usable(history)
    if len(good) < predict.MIN_READY:
        return ("%s%s is still learning your pace: %d of %d turns so far.\n"
                "Ranges start after %d turns." % (mk, NAME, len(good), predict.MIN_READY,
                                                 predict.MIN_READY))
    ev = predict.evaluate(history, coverage)
    durs = sorted(r["dur"] for r in good)
    rows = [("Typical turn", fmt.duration(predict.quantile(durs, 0.5))),
            ("Longest turn", fmt.duration(durs[-1]))]
    # score the range the user sees now: the wide one while learning
    shown = coverage if len(good) >= predict.CONFIDENT else "80"
    b = ev["by_band"].get(shown)
    if b:
        rows.insert(0, ("Inside the range", "%d%% of %d turn%s (aim %s%%)"
                        % (round(b["coverage"] * 100), b["scored"],
                           "" if b["scored"] == 1 else "s", shown)))
    out = ["%s%s · your last %d turns" % (mk, NAME, len(good))]
    out += ["  %-17s %s" % r for r in rows]
    return "\n".join(out)


def main(argv, stdin, stdout, env, now=None) -> int:
    try:
        argv = list(argv)
        host = "claude"
        if "--host" in argv:
            i = argv.index("--host")
            host = argv[i + 1] if i + 1 < len(argv) else ""
            del argv[i:i + 2]
            if host not in hosts.HOSTS:
                return 0
        event = argv[1] if len(argv) > 1 else ""
        now = time.time() if now is None else now
        # Slash commands pass the data dir as an argument: they do not get
        # CLAUDE_PLUGIN_DATA in their environment, only as text substitution.
        if len(argv) > 3 and argv[2] == "--data" and argv[3]:
            env = dict(env, CLAUDE_PLUGIN_DATA=argv[3])
            if host != "claude":
                env["RAINDRAGON_FORECAST_DATA"] = argv[3]
        env = hosts.data_dir_env(env, host)
        if event == "doctor":
            stdout.write(json.dumps(doctor(env, now, host), indent=2) + "\n")
            return 0
        if event == "refresh-status":
            src = status.SOURCES.get(host)
            if src:
                status.refresh(store.data_dir(env), now, url=src[0])
            return 0
        if event == "stats":
            d = store.data_dir(env)
            cov = env.get("CLAUDE_PLUGIN_OPTION_BAND") or "50"
            stdout.write(stats_text(store.read_history(d), cov, mark(env)) + "\n")
            return 0
        if event == "eval":
            d = store.data_dir(env)
            cov = env.get("CLAUDE_PLUGIN_OPTION_BAND") or "50"
            h = store.read_history(d)
            out = predict.evaluate(h, cov)
            if any(r.get("srv_tok") for r in h):
                out["server"] = predict.evaluate_server(h, cov)
            stdout.write(json.dumps(out, indent=2) + "\n")
            return 0
        if event in ("server-predict", "server-outcome"):
            job = json.loads(stdin.read() or "{}")
            url = str(job.get("url") or "")
            if not isinstance(job.get("body"), dict) or not url:
                return 0
            if event == "server-outcome":
                server.tell(url, job["body"])
                return 0
            res = server.ask({"url": url}, job["body"], server.BACKGROUND_TIMEOUT_S)
            server.write_result(store.data_dir(env), store._safe(str(job.get("sid") or "")),
                                dict(res, start=job.get("start")))
            return 0
        if not enabled(env):
            return 0
        inp = json.loads(stdin.read() or "{}")
        if not isinstance(inp, dict):
            return 0
        if event == "prompt":
            msg = on_prompt(inp, env, now, host=host)
        elif event == "stop" and host == "gemini":
            err = hosts.gemini_failed(inp)
            msg = on_stop(inp, env, now, ok=err is None, host=host, error=err)
        elif event == "stop":
            msg = on_stop(inp, env, now, ok=True, host=host)
        elif event == "interrupt" and host == "codex":
            # stopped by the user: never recorded (same rule as everywhere)
            store.pop_pending(store.data_dir(env), inp.get("session_id") or "")
            msg = ""
        elif event == "stop-failure":
            on_stop(inp, env, now, ok=False)
            msg = ""  # StopFailure output is ignored by Claude Code anyway
        else:
            msg = ""
        if msg:
            stdout.write(json.dumps({"systemMessage": msg}))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv, sys.stdin, sys.stdout, dict(os.environ)))
