"""Optional server comparison: RainDragon's TinyLM reply-length API.

    POST {server_url}/v1/eta/predict   -> {prediction_id, output_tokens, model_version}
    POST {server_url}/v1/eta/outcome   <- {prediction_id, install_id, duration_ms, failed}

The API needs the prompt TEXT, so nothing is sent unless the user turns on
"server_send_text". Then the request carries the prompt (first MAX_TEXT_CHARS
characters), a random per-install id, "plugin", the plugin version and the
model name "claude"; never the transcript, file names, paths or session ids,
and always share=false, so the server does not keep the text. The outcome
carries only the prediction id, the install id, the duration and whether the
turn failed.

Modes (option "server_mode"):
    off     nothing is ever sent
    shadow  (default) ask in the background; the range shown stays the local
            one; both predictions and the real duration go into history.jsonl
            so `eval` can compare them on your own turns
    server  show the server's range once MIN_PAIRED turns have both
    blend   show the geometric mean of the two ranges, same condition

Anything going wrong (offline, timeout, 4xx/5xx, 503 while the model is not
loaded, garbage) means the local range, silently. The prompt hook never waits
longer than PROMPT_TIMEOUT_S, and in shadow mode not at all: the request runs in
a detached process that gets the prompt on stdin (never argv, never a file) and
leaves only the numbers in pending/<session>.srv for the Stop hook.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Callable, Optional

DEFAULT_URL = "https://inference.raindragon.ai"
MODES = ("off", "shadow", "server", "blend")
MAX_TEXT_CHARS = 8000          # the model reads at most 2048 tokens
PROMPT_TIMEOUT_S = 0.8         # server/blend: the longest the prompt hook waits
BACKGROUND_TIMEOUT_S = 5.0     # shadow and outcome requests


def settings(env: dict) -> dict:
    def opt(key, default=""):
        v = env.get("CLAUDE_PLUGIN_OPTION_" + key)
        return default if v is None or v == "" else str(v).strip()
    mode = opt("SERVER_MODE", "shadow").lower()
    url = opt("SERVER_URL", DEFAULT_URL).rstrip("/")
    if not url.startswith("https://") and not url.startswith("http://127.0.0.1"):
        url = DEFAULT_URL
    return {"mode": mode if mode in MODES else "shadow",
            "send_text": opt("SERVER_SEND_TEXT", "false").lower() in ("1", "true", "yes", "on"),
            "url": url}


def active(cfg: dict) -> bool:
    """True only when the user allowed sending prompt text and the mode is not off."""
    return cfg["mode"] != "off" and cfg["send_text"]


def install_id(d: str) -> str:
    path = os.path.join(d, "install_id")
    try:
        with open(path) as f:
            v = f.read().strip()
        if 8 <= len(v) <= 64:
            return v
    except OSError:
        pass
    v = str(uuid.uuid4())
    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write(v)
        os.replace(tmp, path)
    except OSError:
        pass
    return v


def _post(url: str, body: dict, timeout: float) -> Optional[dict]:
    import urllib.request
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "raindragon-forecast-plugin"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(65536)
        return json.loads(raw) if raw else {}


def predict_body(iid: str, version: str, prompt: str) -> dict:
    return {"install_id": iid, "client": "plugin", "client_version": version,
            "model": "claude", "text": prompt[:MAX_TEXT_CHARS], "share": False}


def ask(cfg: dict, body: dict, timeout: float, post: Callable = None) -> dict:
    """{"pid", "tok", "ver", "ms"} or {"err": <kind>, "ms"}. Never raises."""
    t0 = time.time()
    try:
        j = (post or _post)(cfg["url"] + "/v1/eta/predict", body, timeout)
        tok = j.get("output_tokens") if isinstance(j, dict) else None
        pid = j.get("prediction_id")
        if isinstance(tok, bool) or not isinstance(tok, (int, float)) or tok < 1 \
                or not isinstance(pid, str) or not pid.startswith("etp_"):
            return {"err": "bad_answer", "ms": round((time.time() - t0) * 1000)}
        return {"pid": pid[:40], "tok": int(tok), "ver": str(j.get("model_version") or "")[:40],
                "ms": round((time.time() - t0) * 1000)}
    except Exception as e:  # noqa: BLE001  offline, timeout, HTTP error: all mean local
        return {"err": _kind(e), "ms": round((time.time() - t0) * 1000)}


def _kind(e: Exception) -> str:
    code = getattr(e, "code", None)
    if isinstance(code, int):
        return "http_%d" % code
    if isinstance(e, TimeoutError) or "timed out" in str(e).lower():
        return "timeout"
    return "error"


def outcome_body(iid: str, pid: str, dur_s: float, ok: bool) -> dict:
    return {"prediction_id": pid, "install_id": iid, "duration_ms": int(round(dur_s * 1000)),
            "failed": not ok}


def tell(url: str, body: dict, post: Callable = None) -> None:
    try:
        (post or _post)(url + "/v1/eta/outcome", body, BACKGROUND_TIMEOUT_S)
    except Exception:  # noqa: BLE001
        pass


def write_result(d: str, sid_file: str, res: dict) -> None:
    path = os.path.join(d, "pending", sid_file + ".srv")
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(res, f)
        os.replace(tmp, path)
    except OSError:
        pass


def pop_result(d: str, sid_file: str) -> Optional[dict]:
    path = os.path.join(d, "pending", sid_file + ".srv")
    try:
        with open(path) as f:
            v = json.load(f)
    except (OSError, ValueError):
        return None
    try:
        os.remove(path)
    except OSError:
        pass
    return v if isinstance(v, dict) else None


def spawn(event: str, d: str, payload: dict, host: str = "claude") -> None:
    """Run `hook.py <event> --data d` detached; the payload goes over stdin so
    the prompt never appears in a process list or on disk."""
    import subprocess
    import sys
    hook = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "raindragon_eta_hook.py")
    p = subprocess.Popen([sys.executable, hook, event, "--data", d, "--host", host],
                         stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)
    try:
        p.stdin.write(json.dumps(payload).encode())
        p.stdin.close()
    except OSError:
        pass
