#!/usr/bin/env python3
"""Write tests/fixtures/turn_model/cases.json: synthetic inputs with the features
and quantiles the Python model gives, so tests/turn_model.test.js can hold the
JS twin to the same numbers. Rerun after retraining turn_model.json."""
import json
import math
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugin", "scripts"))
from raindragon_eta import predict, turn_model  # noqa: E402

OUT = os.path.join(HERE, "..", "tests", "fixtures", "turn_model", "cases.json")


def clean(f):
    return {k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in f.items()}


def bucket_cases(m):
    """The extension buckets prompt and conversation sizes its own way and passes
    the buckets in (turn_model.js features(..., {pb, cb})): user_features with
    those buckets, here."""
    rnd = random.Random(11)
    out = []
    for _ in range(30):
        n_hist = rnd.choice([0, 3, 9, 15, 45, 200])
        hist = [{"dur": round(rnd.lognormvariate(2.8, 0.9), 2), "ok": rnd.random() > 0.05,
                 "incident": rnd.random() < 0.05, "pb": rnd.choice(["short", "medium", "long"]),
                 "cb": rnd.choice(["small", "medium", "large"]),
                 "effort": rnd.choice(["opus 5.5 medium", "sonnet 5"])} for _ in range(n_hist)]
        k = rnd.choice([0, 1, 4, 12])
        now = 1.7e9
        done = [[now - 45 * (j + 1), round(rnd.lognormvariate(2.8, 0.8), 2)] for j in range(min(k, 10))][::-1]
        sess = {"n": k + rnd.choice([0, 2]), "k": k, "done": done}
        pc = rnd.choice([0, 5, 150, 900, 6000])
        pb, cb = rnd.choice(["short", "medium", "long"]), rnd.choice(["small", "medium", "large"])
        effort = rnd.choice(["opus 5.5 medium", "sonnet 5", None])
        f = {"lp": math.log1p(max(0, pc))}
        f.update(turn_model.session_features(sess, now))
        f.update(turn_model.user_features(predict.usable(hist), pb, cb, effort))
        out.append({"prompt_chars": pc, "pb": pb, "cb": cb, "effort": effort, "history": hist,
                    "session": sess, "now": now, "features": clean(f),
                    "quantiles": turn_model.quantiles(m, f)})
    return out


def choose_cases():
    """turn_model.choose and evaluate_live on histories that recorded both bands."""
    rnd = random.Random(13)
    out = []
    for i in range(30):
        n = rnd.choice([5, 19, 20, 21, 60, 120])
        hist = []
        for _ in range(n):
            dur = round(rnd.lognormvariate(3, 0.8), 2)
            r = {"dur": dur, "ok": rnd.random() > 0.05, "incident": False, "pb": "short", "cb": "small"}
            for key, skew in (("loc", rnd.uniform(0.5, 2.5)), ("glb", rnd.uniform(0.5, 2.5))):
                if rnd.random() < 0.9:
                    mid = dur * skew * rnd.lognormvariate(0, 0.5)
                    r[key] = [round(mid / 1.6, 2), round(mid * 1.6, 2)]
            if r.get("glb"):
                r["shown"] = rnd.choice(["global", "local"])
            hist.append(r)
        local = None if i % 7 == 0 else predict.Band(1.0, 2.0, 10, "all", "50", learning=(i % 5 == 0))
        glb = None if i % 11 == 0 else predict.Band(3.0, 4.0, 10, "global", "50")
        band, shown = turn_model.choose(glb, local, hist)
        out.append({"history": hist, "local": None if local is None else {"learning": local.learning},
                    "global": glb is not None, "local_is_better": turn_model.local_is_better(hist),
                    "shown": shown, "evaluate_live": turn_model.evaluate_live(hist)})
    return out


def main():
    rnd = random.Random(7)
    m = turn_model.load()
    cases = []
    for i in range(40):
        n_hist = rnd.choice([0, 2, 5, 12, 40, 120])
        hist = [{"dur": round(rnd.lognormvariate(4, 1.2), 2), "ok": True, "incident": False,
                 "pb": rnd.choice(["short", "medium", "long"]), "cb": rnd.choice(["small", "medium"]),
                 "effort": "high"} for _ in range(n_hist)]
        k = rnd.choice([0, 1, 3, 15])
        now = 100000.0
        done = [[now - 60 * (j + 1), round(rnd.lognormvariate(3.5, 1), 2)] for j in range(min(k, 10))][::-1]
        sess = {"n": k + rnd.choice([0, 1]), "k": k, "done": done}
        pc = rnd.choice([3, 40, 250, 1800, 9000])
        tb = rnd.choice([0, 50_000, 400_000, 2_000_000])
        f = turn_model.features(pc, tb, "high", hist, sess, now)
        cases.append({"prompt_chars": pc, "transcript_bytes": tb, "effort": "high", "history": hist,
                      "session": sess, "now": now, "features": clean(f),
                      "quantiles": turn_model.quantiles(m, f)})
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        json.dump({"model_version": m["version"], "cases": cases,
                   "bucket_cases": bucket_cases(m), "choose_cases": choose_cases()}, fh, indent=1)
    print("wrote", OUT, len(cases))


if __name__ == "__main__":
    main()
