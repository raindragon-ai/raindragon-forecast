"""Local state. Nothing here leaves the machine.

history.jsonl  one line per finished turn: duration and coarse size buckets.
               No prompt text, no file names, no session ids.
pending/<sid>  the turn currently running in a session (start time + the
               estimate we showed), removed when the turn ends.
noted/<sid>    marker that we already told this session we are still learning.
sessions/<sid> this session's prompt count and its last few turn durations
               (numbers only), the global model's session features.

The tool was called RainDragon ETA before 0.2.x became RainDragon Forecast.
Renaming the plugin gives it a new data dir, so the first run copies the old
history across (migrate_old_history) and nobody starts learning from zero.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from typing import Optional

MAX_HISTORY_BYTES = 512_000
KEEP_LINES = 1000


HOME_DIR = ".raindragon-forecast"
OLD_HOME_DIR = ".raindragon-eta"   # name before the rename to Forecast


def _home(env: dict) -> str:
    return env.get("HOME") or os.path.expanduser("~")


def data_dir(env: dict) -> str:
    d = env.get("CLAUDE_PLUGIN_DATA") or os.path.join(_home(env), HOME_DIR)
    os.makedirs(os.path.join(d, "pending"), exist_ok=True)
    os.makedirs(os.path.join(d, "noted"), exist_ok=True)
    os.makedirs(os.path.join(d, "sessions"), exist_ok=True)
    migrate_old_history(env, d)
    return d


def old_data_dirs(env: dict, d: str) -> list:
    """Where a RainDragon ETA install kept its history, newest guess first:
    the data dir the old plugin's pointer named, the old fallback folder, and
    the same folder name with -eta for the Codex / Gemini hosts."""
    home = _home(env)
    out = []
    try:
        with open(os.path.join(home, OLD_HOME_DIR, "data_dir")) as f:
            out.append(f.read().strip())
    except OSError:
        pass
    out.append(os.path.join(home, OLD_HOME_DIR))
    if os.path.basename(d) == "raindragon-forecast":
        out.append(os.path.join(os.path.dirname(d), "raindragon-eta"))
    return [p for p in out if p and os.path.abspath(p) != os.path.abspath(d)]


def migrate_old_history(env: dict, d: str) -> None:
    """Copy (never move) the old history and settings into a new data dir that
    has none yet. Runs once: afterwards the new dir has a history.jsonl."""
    if os.path.exists(os.path.join(d, "history.jsonl")):
        return
    for old in old_data_dirs(env, d):
        src = os.path.join(old, "history.jsonl")
        if not os.path.isfile(src):
            continue
        try:
            for name in ("settings.json", "history.jsonl"):   # history last: it marks done
                if os.path.isfile(os.path.join(old, name)) and not os.path.exists(os.path.join(d, name)):
                    shutil.copyfile(os.path.join(old, name), os.path.join(d, name + ".tmp"))
                    os.replace(os.path.join(d, name + ".tmp"), os.path.join(d, name))
        except OSError:
            pass
        return


def _safe(session_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "nosession")[:128]


def read_history(d: str) -> list:
    out = []
    try:
        with open(os.path.join(d, "history.jsonl")) as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def append_history(d: str, record: dict) -> None:
    path = os.path.join(d, "history.jsonl")
    with open(path, "a") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")
    try:
        if os.path.getsize(path) > MAX_HISTORY_BYTES:
            with open(path) as f:
                lines = f.readlines()[-KEEP_LINES:]
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                f.writelines(lines)
            os.replace(tmp, path)
    except OSError:
        pass


def write_pending(d: str, session_id: str, pending: dict) -> None:
    path = os.path.join(d, "pending", _safe(session_id))
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(pending, f)
    os.replace(tmp, path)


def pop_pending(d: str, session_id: str) -> Optional[dict]:
    path = os.path.join(d, "pending", _safe(session_id))
    try:
        with open(path) as f:
            p = json.load(f)
    except (OSError, ValueError):
        return None
    try:
        os.remove(path)
    except OSError:
        pass
    return p


def cleanup(d: str, now: float, max_age: float = 2 * 86400) -> None:
    """Drop per-session markers older than max_age so they don't pile up."""
    for sub in ("pending", "noted", "sessions"):
        folder = os.path.join(d, sub)
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            path = os.path.join(folder, name)
            try:
                if now - os.path.getmtime(path) > max_age:
                    os.remove(path)
            except OSError:
                pass


SESSION_KEEP = 10


def read_session(d: str, session_id: str) -> dict:
    """{"n": prompts so far, "k": turns finished, "done": last SESSION_KEEP [end, dur]}."""
    try:
        with open(os.path.join(d, "sessions", _safe(session_id))) as f:
            s = json.load(f)
        if isinstance(s, dict):
            return s
    except (OSError, ValueError):
        pass
    return {"n": 0, "k": 0, "done": []}


def _write_session(d: str, session_id: str, s: dict) -> None:
    path = os.path.join(d, "sessions", _safe(session_id))
    try:
        with open(path + ".tmp", "w") as f:
            json.dump(s, f, separators=(",", ":"))
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def session_prompted(d: str, session_id: str) -> None:
    s = read_session(d, session_id)
    s["n"] = int(s.get("n") or 0) + 1
    _write_session(d, session_id, s)


def session_finished(d: str, session_id: str, end: float, dur: float) -> None:
    s = read_session(d, session_id)
    s["k"] = int(s.get("k") or 0) + 1
    s["done"] = (list(s.get("done") or []) + [[round(end, 1), round(dur, 2)]])[-SESSION_KEEP:]
    _write_session(d, session_id, s)


def mark_noted(d: str, session_id: str) -> bool:
    """True the first time for a session, False after."""
    path = os.path.join(d, "noted", _safe(session_id))
    if os.path.exists(path):
        return False
    try:
        open(path, "w").close()
    except OSError:
        pass
    return True


def write_json(d: str, name: str, obj: dict) -> None:
    path = os.path.join(d, name)
    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(obj, f)
        os.replace(tmp, path)
    except OSError:
        pass


def read_json(d: str, name: str) -> Optional[dict]:
    try:
        with open(os.path.join(d, name)) as f:
            v = json.load(f)
        return v if isinstance(v, dict) else None
    except (OSError, ValueError):
        return None


POINTER = os.path.join(HOME_DIR, "data_dir")


def write_pointer(env: dict, d: str) -> None:
    """Leave the data dir's path at ~/.raindragon-forecast/data_dir for the live
    status-line mod (plugin/live/live.ts): a mod is told its plugin's root but
    not its data dir, which Claude Code names after the install id. Written
    only when it changed."""
    path = os.path.join(_home(env), POINTER)
    try:
        with open(path) as f:
            if f.read().strip() == d:
                return
    except OSError:
        pass
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write(d)
        os.replace(tmp, path)
    except OSError:
        pass
