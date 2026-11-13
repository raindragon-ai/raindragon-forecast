"""Which terminal agent is calling the hook, and how to read its payloads.

The same hook script times turns in three CLIs. Each host has its own data
folder, so histories never mix (a Codex turn says nothing about a Claude Code
turn), and its own way of naming the turn and the "similar" key.

  claude  Claude Code plugin (default). UserPromptSubmit / Stop / StopFailure.
  codex   OpenAI Codex CLI. UserPromptSubmit / Stop / Interrupt
          (learn.chatgpt.com/docs/hooks). Paired by `turn_id`.
  gemini  Gemini CLI. BeforeAgent / AfterAgent
          (geminicli.com/docs/hooks/reference). No turn id: paired by session.

Only lengths, timings and model/effort labels are read from a payload. The
prompt text is measured (its length) and dropped.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from . import store

HOSTS = ("claude", "codex", "gemini")

#: Data folder per host, under the host's own config dir.
DATA_SUBDIR = {"codex": (".codex", "raindragon-forecast"), "gemini": (".gemini", "raindragon-forecast")}

#: Settings for hosts without plugin options live in <data>/settings.json,
#: e.g. {"band": "80", "show_result": true, "check_status": false}. Hooks in
#: Gemini CLI run in a sanitized environment, so a file is the reliable place.
SETTING_KEYS = {"enabled": "ENABLED", "band": "BAND", "show_result": "SHOW_RESULT",
                "check_status": "CHECK_STATUS", "mark": "MARK", "model": "MODEL"}

ROLLOUT_TAIL_BYTES = 1_000_000  # read at most the last 1 MB of a Codex rollout


def home(env: dict) -> str:
    return env.get("HOME") or os.path.expanduser("~")


def data_dir_env(env: dict, host: str) -> dict:
    """The env the rest of the hook sees: CLAUDE_PLUGIN_DATA points at this
    host's folder, and settings.json is mapped onto the plugin-option names."""
    if host == "claude":
        return env
    env = dict(env)
    # RAINDRAGON_ETA_DATA is the name from before the rename; still honoured
    d = (env.get("RAINDRAGON_FORECAST_DATA") or env.get("RAINDRAGON_ETA_DATA")
         or os.path.join(home(env), *DATA_SUBDIR[host]))
    env["CLAUDE_PLUGIN_DATA"] = d
    try:
        os.makedirs(d, exist_ok=True)
        store.migrate_old_history(env, d)   # before settings.json is read below
    except OSError:
        pass
    for k in list(env):
        if k.startswith("CLAUDE_PLUGIN_OPTION_"):
            del env[k]   # never inherit Claude Code's options into another host
    try:
        with open(os.path.join(d, "settings.json")) as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    if isinstance(cfg, dict):
        for key, opt in SETTING_KEYS.items():
            if key in cfg and cfg[key] is not None:
                v = cfg[key]
                env["CLAUDE_PLUGIN_OPTION_" + opt] = ("true" if v else "false") if isinstance(v, bool) else str(v)
    return env


def turn_key(inp: dict, host: str) -> Optional[str]:
    """The id that pairs start and end of one turn, if the host gives one."""
    if host == "codex":
        return inp.get("turn_id")
    if host == "gemini":
        return None
    return inp.get("prompt_id")


def similar_label(inp: dict, host: str) -> Optional[str]:
    """Third "similar" level (the Claude plugin's effort level). Codex: the
    model name from the payload, which is known before the turn runs. Gemini's
    BeforeAgent payload names no model, so there is none."""
    if host == "codex":
        m = inp.get("model")
        return str(m)[:64] if m else None
    if host == "gemini":
        return None
    return (inp.get("effort") or {}).get("level")


# ---------- Codex rollout ----------

def _tail_lines(path: str, max_bytes: int = ROLLOUT_TAIL_BYTES):
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - max_bytes))
        data = f.read()
    lines = data.split(b"\n")
    if size > max_bytes:
        lines = lines[1:]   # first line is cut
    return lines


def codex_turn_outcome(transcript_path: Optional[str], turn_id: Optional[str]) -> Optional[dict]:
    """What Codex's own rollout log says about one turn, or None if it says
    nothing (yet).

    Reads `event_msg` records whose payload is `task_complete` (alias
    `turn_complete`) or `turn_aborted` for `turn_id`, and returns only:
      {"kind": "complete"|"aborted", "duration_ms": int|None, "error": str|None}
    where error is the `codex_error_info` variant name (e.g.
    "server_overloaded"), never the error message text.

    Codex writes task_complete AFTER the Stop hook runs, and does not run
    Stop at all when a turn ends in an error, so this is read at the NEXT
    prompt of the session, for a turn whose Stop never came."""
    if not transcript_path or not turn_id or transcript_path.endswith(".zst"):
        return None
    try:
        lines = _tail_lines(transcript_path)
    except OSError:
        return None
    found = None
    needle = turn_id.encode()
    for raw in lines:
        if needle not in raw:
            continue
        try:
            line = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(line, dict) or line.get("type") != "event_msg":
            continue
        p = line.get("payload")
        if not isinstance(p, dict) or p.get("turn_id") != turn_id:
            continue
        t = p.get("type")
        if t in ("task_complete", "turn_complete"):
            kind = "complete"
        elif t == "turn_aborted":
            kind = "aborted"
        else:
            continue
        err = p.get("error")
        error = None
        if isinstance(err, dict):
            info = err.get("codex_error_info")
            if isinstance(info, str):
                error = info
            elif isinstance(info, dict) and info:
                error = next(iter(info))
            else:
                error = "error"
        elif err:
            error = "error"
        dur = p.get("duration_ms")
        found = {"kind": kind, "duration_ms": dur if isinstance(dur, (int, float)) else None,
                 "error": error[:64] if error else None}
    return found


# ---------- Gemini ----------

#: Gemini CLI passes this when the turn produced no text (client.ts,
#: fireAfterAgentHookSafe). An empty reply is not a normal turn.
GEMINI_NO_TEXT = "[no response text]"


def gemini_failed(inp: dict) -> Optional[str]:
    """Error type if an AfterAgent payload looks like a failed turn."""
    r = inp.get("prompt_response")
    if r is None or not str(r).strip() or str(r).strip() == GEMINI_NO_TEXT:
        return "empty_response"
    return None
