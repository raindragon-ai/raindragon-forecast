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
from raindragon_eta import turn_model  # noqa: E402

OUT = os.path.join(HERE, "..", "tests", "fixtures", "turn_model", "cases.json")


def clean(f):
    return {k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in f.items()}


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
        json.dump({"model_version": m["version"], "cases": cases}, fh, indent=1)
    print("wrote", OUT, len(cases))


if __name__ == "__main__":
    main()
