"""Turn duration band from one global model, shared by every user.

The local lookup (predict.py) needs 10 of the user's own turns before it can say
anything and 30 before its narrow band. This model answers from the first turn.
It was trained once, offline (tools/train_turn_model.py), on public
coding-agent sessions from 337 people. The file holds numbers only: no prompt
text, ids or strings. Nothing is fitted to the user. What the user has done so
far enters only as INPUT, the same way the prompt's length does:

  * this prompt: its length, and how many prompts came before it in the session
  * this session: the last two turn durations, the median of the last ten, how
    many have finished, and the time since the last one ended
  * this user's recent turns, when there are any: quantiles of the last 300 and
    the local lookup's own band (predict._predict_buckets)

Five LightGBM quantile models (10/25/50/75/90%) of ln(seconds), walked here in
plain Python from turn_model.json. Their bands were widened (or narrowed) on
held-out people so that about half land in the 50% band and 8 in 10 in the 80%.

Held-out people (5-fold, 337 people, 72K turns): the band's middle within 2x of
the real duration 39.8% of the time, against 35.8% for the local lookup where it
shows anything; coverage 50.4% / 80.2% at nominal 50 / 80. On a person's first
10 turns, where the local lookup is silent, 36.3%.
"""

from __future__ import annotations

import json
import math
import os
from typing import Iterable, Optional

from . import predict

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(HERE, "turn_model.json")
SESSION_KEEP = 10      # finished turns kept per session
USER_MIN = 3           # user turns before their quantiles are used
NAN = float("nan")
QUANTILES = (0.1, 0.25, 0.5, 0.75, 0.9)

_cache: dict = {}


def load(path: str = MODEL_PATH) -> Optional[dict]:
    """The model, or None if the file is missing or unreadable. Never raises."""
    if path in _cache:
        return _cache[path]
    m = None
    try:
        with open(path) as f:
            raw = json.load(f)
        if [float(q) for q in raw["quantiles"]] != list(QUANTILES):
            raise ValueError("unexpected quantiles")
        unknown = [n for n in raw["features"] if n not in FEATURES]
        if unknown:
            raise ValueError("unknown features %s" % unknown)
        m = raw
    except (OSError, ValueError, KeyError, TypeError):
        m = None
    _cache[path] = m
    return m


def _lg(x: float) -> float:
    return math.log(x) if x and x > 0 else NAN


def _median(v: list) -> float:
    s = sorted(v)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def session_features(session: Optional[dict], now: float) -> dict:
    """session = {"n": prompts before this one, "k": turns finished, "done": the last
    SESSION_KEEP as [end, dur]} (store.read_session)."""
    s = session or {}
    kept = [d for d in s.get("done") or [] if isinstance(d, list) and len(d) == 2 and d[1] > 0]
    done = [d for d in kept if d[0] <= now]
    later = len(kept) - len(done)
    f = {"lprior": math.log1p(max(0, int(s.get("n") or 0))),
         "s_n": float(max(len(done), int(s.get("k") or 0) - later)),
         "s_prev1": NAN, "s_prev2": NAN, "s_med": NAN, "s_gap": NAN}
    if done:
        f["s_prev1"] = math.log(done[-1][1])
        if len(done) > 1:
            f["s_prev2"] = math.log(done[-2][1])
        f["s_med"] = math.log(_median([d[1] for d in done[-SESSION_KEEP:]]))
        f["s_gap"] = math.log1p(max(0.0, now - done[-1][0]))
    return f


def user_features(good: list, pb: str, cb: str, effort: Optional[str]) -> dict:
    """good = predict.usable(history): the user's recent finished turns, oldest first."""
    f = {k: NAN for k in ("u_med", "u_q25", "u_q75", "u_q10", "u_q90", "u_med5", "loc_lo", "loc_hi")}
    f["u_n"] = float(len(good))
    if len(good) >= USER_MIN:
        lh = [math.log(r["dur"]) for r in good]
        s = sorted(lh)
        f["u_med"] = _median(lh)
        f["u_q25"], f["u_q75"] = predict.quantile(s, 0.25), predict.quantile(s, 0.75)
        f["u_q10"], f["u_q90"] = predict.quantile(s, 0.10), predict.quantile(s, 0.90)
        f["u_med5"] = _median(lh[-5:])
    b = predict._predict_buckets(good, pb, cb, effort, "50")
    if b is not None:
        f["loc_lo"], f["loc_hi"] = _lg(b.low), _lg(b.high)
    return f


FEATURES = ("lp", "lprior", "s_n", "s_prev1", "s_prev2", "s_med", "s_gap",
            "u_n", "u_med", "u_q25", "u_q75", "u_q10", "u_q90", "u_med5", "loc_lo", "loc_hi")


def features(prompt_chars: int, transcript_bytes: int, effort: Optional[str],
             history: Iterable[dict], session: Optional[dict], now: float) -> dict:
    f = {"lp": math.log1p(max(0, int(prompt_chars or 0)))}
    f.update(session_features(session, now))
    f.update(user_features(predict.usable(history), predict.prompt_bucket(prompt_chars or 0),
                           predict.context_bucket(transcript_bytes or 0), effort))
    return f


def _tree_sum(t: dict, x: list) -> float:
    """LightGBM's numerical decision (as app/length_learned.evaluate in the gateway):
    NaN counts as 0 unless the split's missing type is NaN; a missing value, or a
    zero under missing type Zero, takes the default branch."""
    feat, thr, left, right, dleft, miss, leaf = (t["feat"], t["thr"], t["left"], t["right"],
                                                 t["dleft"], t["miss"], t["leaf"])
    total = 0.0
    for node in t["roots"]:
        while node >= 0:
            v = x[feat[node]]
            nan = v != v
            m = miss[node]
            v0 = 0.0 if (nan and m != 2) else v
            if (m == 1 and abs(v0) <= 1e-35) or (m == 2 and nan):
                go_left = bool(dleft[node])
            else:
                go_left = v0 <= thr[node]
            node = left[node] if go_left else right[node]
        total += leaf[~node]
    return total


def quantiles(model: dict, f: dict) -> list:
    """ln(seconds) at QUANTILES, calibrated and non-crossing."""
    x = [f.get(n, NAN) for n in model["features"]]
    q = sorted(_tree_sum(t, x) for t in model["trees"])
    w50, w80 = model["widen"]["50"], model["widen"]["80"]
    q[1] -= w50
    q[3] += w50
    q[0] = min(q[0] - w80, q[1])
    q[4] = max(q[4] + w80, q[3])
    return q


def band(model: Optional[dict], f: dict, n: int, coverage: str = "50") -> Optional[predict.Band]:
    """The global band (seconds). None without a model. Never raises."""
    if not model:
        return None
    try:
        q = quantiles(model, f)
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    lo, hi = (q[0], q[4]) if coverage == "80" else (q[1], q[3])
    return predict.Band(math.exp(lo), math.exp(hi), n, "global", "80" if coverage == "80" else "50")


# ---------- which band to show ----------
#
# The global band from the first turn. Once the local lookup is past its learning
# phase, the user's own finished turns decide between the two: whichever band's
# middle was within 2x of the real duration more often over the last PICK_WINDOW
# turns that recorded both (ties keep the global one). Nothing is fitted. On the
# held-out people this keeps nearly all of the global gain (39.6% vs 39.9%
# within-2x); on the one long real plugin history we have (7.9K turns) it lands
# at 37.5%, against 37.9% for local alone and 36.5% for global alone.

PICK_WINDOW = 50
PICK_MIN = 20


def _hit(b, dur: float) -> bool:
    return (isinstance(b, list) and len(b) == 2 and all(isinstance(v, (int, float)) and v > 0 for v in b)
            and abs(math.log(math.sqrt(b[0] * b[1]) / dur)) <= math.log(2))


def local_is_better(history: Iterable[dict]) -> bool:
    both = [r for r in predict.usable(history) if r.get("glb") and r.get("loc")][-PICK_WINDOW:]
    if len(both) < PICK_MIN:
        return False
    return sum(_hit(r["loc"], r["dur"]) for r in both) > sum(_hit(r["glb"], r["dur"]) for r in both)


def choose(glb: Optional[predict.Band], local: Optional[predict.Band],
           history: Iterable[dict]) -> tuple:
    """(band, "global" | "local")."""
    if glb is None:
        return local, "local"
    if local is not None and not local.learning and local_is_better(history):
        return local, "local"
    return glb, "global"


def evaluate_live(history: Iterable[dict]) -> dict:
    """On finished turns that recorded the global band: share inside each band and
    share whose middle was within 2x, for global, local and what was shown."""
    rows = [r for r in predict.usable(history) if r.get("glb")]

    def shown(r):
        return r["loc"] if r.get("shown") == "local" and r.get("loc") else r["glb"]

    out = {"turns": len(rows)}
    for name, pick in (("global", lambda r: r["glb"]), ("local", lambda r: r.get("loc")),
                       ("shown", shown)):
        got = [(pick(r), r["dur"]) for r in rows if pick(r)]
        n = len(got)
        out[name] = {"scored": n,
                     "coverage": sum(b[0] <= d <= b[1] for b, d in got) / n if n else None,
                     "within_2x": sum(_hit(b, d) for b, d in got) / n if n else None}
    return out
