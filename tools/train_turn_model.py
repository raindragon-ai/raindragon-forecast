#!/usr/bin/env python3
"""Train plugin/scripts/raindragon_eta/turn_model.json (offline; needs numpy,
scikit-learn and lightgbm, which the plugin itself never imports).

Input: gzipped JSONL, one row per finished agent turn, with NO text:
    user, session, t (start, epoch s), dur (s), prompt_chars, [transcript_bytes]
Each person's turns are replayed the way the hook sees them (history appended at
Stop, session state per session), and the features come from turn_model.features
itself, so training and serving compute them with the same code.

    tools/train_turn_model.py OUT.json turns_a.jsonl.gz [turns_b.jsonl.gz ...] [--louo]

--louo first prints a 5-fold leave-people-out check of this exact config against
the local lookup. The shipped file came from public coding-agent sessions of 337
people (SWE-chat mirror, DataClaw exports, other public Claude Code / Codex / Pi
uploads, OpenHands feedback); see the PR that added it for sources and results.
"""
import gzip
import heapq
import json
import math
import os
import sys

import lightgbm as lgb
import numpy as np
from sklearn.model_selection import GroupKFold

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugin", "scripts"))
from raindragon_eta import predict as P  # noqa: E402
from raindragon_eta import turn_model as T  # noqa: E402

PARAMS = dict(learning_rate=0.15, num_leaves=15, min_data_in_leaf=200, feature_fraction=0.8,
              bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, verbose=-1, seed=7,
              num_threads=4)
ROUNDS = 60
L2 = math.log(2)


def read(paths):
    rows = []
    for i, p in enumerate(paths):
        with gzip.open(p, "rt") as f:
            for line in f:
                r = json.loads(line)
                if r.get("prompt_chars") is None or not (0 < r.get("dur", 0) <= 6 * 3600):
                    continue
                r["user"], r["session"] = "%d:%s" % (i, r["user"]), "%d:%s" % (i, r["session"])
                rows.append(r)
    rows.sort(key=lambda r: (r["t"], r["dur"]))
    seen, out = set(), []
    for r in rows:     # one row per (session, start): later cumulative Stops are dropped
        if (r["session"], r["t"]) not in seen:
            seen.add((r["session"], r["t"]))
            out.append(r)
    return out


def replay(rows):
    """Feature rows as the hook would build them, plus the local bands it would show."""
    by_user = {}
    for r in rows:
        by_user.setdefault(r["user"], []).append(r)
    X, y, grp, loc, idx = [], [], [], [], []
    for u, rs in by_user.items():
        hist, sess, ends = [], {}, []
        for i, r in enumerate(rs):
            while ends and ends[0][0] <= r["t"]:
                e, j = heapq.heappop(ends)
                q = rs[j]
                hist.append({"dur": q["dur"], "pb": q["_pb"], "cb": q["_cb"], "effort": None, "ok": True})
                s = sess.setdefault(q["session"], {"n": 0, "k": 0, "done": []})
                s["done"] = (s["done"] + [[e, q["dur"]]])[-T.SESSION_KEEP:]
                s["k"] += 1
            tb = int(r.get("transcript_bytes") or 0)
            r["_pb"], r["_cb"] = P.prompt_bucket(r["prompt_chars"]), P.context_bucket(tb)
            s = sess.setdefault(r["session"], {"n": 0, "k": 0, "done": []})
            f = T.features(r["prompt_chars"], tb, None, hist, s, r["t"])
            X.append([f[n] for n in T.FEATURES])
            y.append(math.log(r["dur"]))
            grp.append(u)
            lb = P.predict(hist, r["prompt_chars"], tb, None, "50")
            loc.append((math.log(lb.low) + math.log(lb.high)) / 2 if lb else np.nan)
            idx.append(len(P.usable(hist)))
            s["n"] += 1
            heapq.heappush(ends, (r["t"] + r["dur"], i))
    return np.array(X, float), np.array(y), np.array(grp), np.array(loc), np.array(idx)


def fit(X, y):
    return [lgb.train(dict(PARAMS, objective="quantile", alpha=q), lgb.Dataset(X, y), ROUNDS)
            for q in T.QUANTILES]


def raw(ms, X):
    return np.sort(np.column_stack([m.predict(X) for m in ms]), axis=1)


def fit_calibrated(X, y, grp, seed=0):
    """Fit on 80% of people, widen the bands on the other 20% to nominal, refit on all."""
    users = np.unique(grp)
    cal = np.isin(grp, np.random.default_rng(seed).choice(users, max(1, len(users) // 5), replace=False))
    Q, yc = raw(fit(X[~cal], y[~cal]), X[cal]), y[cal]
    w = {"50": float(np.quantile(np.maximum(Q[:, 1] - yc, yc - Q[:, 3]), 0.5)),
         "80": float(np.quantile(np.maximum(Q[:, 0] - yc, yc - Q[:, 4]), 0.8))}
    return fit(X, y), w


def calibrated(Q, w):
    Q = Q.copy()
    Q[:, 1] -= w["50"]
    Q[:, 3] += w["50"]
    Q[:, 0] = np.minimum(Q[:, 0] - w["80"], Q[:, 1])
    Q[:, 4] = np.maximum(Q[:, 4] + w["80"], Q[:, 3])
    return Q


def pack(booster):
    m = {k: [] for k in ("feat", "thr", "left", "right", "dleft", "miss", "leaf", "roots")}
    miss = {"None": 0, "Zero": 1, "NaN": 2}

    def walk(n):
        if "leaf_value" in n:
            m["leaf"].append(round(float(n["leaf_value"]), 7))
            return ~(len(m["leaf"]) - 1)
        if n.get("decision_type", "<=") != "<=":
            raise ValueError("categorical split: train numeric features only")
        i = len(m["thr"])
        for k in ("feat", "thr", "left", "right", "dleft", "miss"):
            m[k].append(0)
        m["feat"][i] = int(n["split_feature"])
        m["thr"][i] = float(n["threshold"])
        m["dleft"][i] = int(bool(n["default_left"]))
        m["miss"][i] = miss[n.get("missing_type", "None")]
        m["left"][i] = walk(n["left_child"])
        m["right"][i] = walk(n["right_child"])
        return i

    for t in booster.dump_model()["tree_info"]:
        m["roots"].append(walk(t["tree_structure"]))
    return m


def louo(X, y, grp, loc, idx):
    Q = np.full((len(y), 5), np.nan)
    for tr, te in GroupKFold(5).split(X, groups=grp):
        ms, w = fit_calibrated(X[tr], y[tr], grp[tr])
        Q[te] = calibrated(raw(ms, X[te]), w)
    for name, m in (("all", np.ones(len(y), bool)), ("first 10", idx < 10), ("first 30", idx < 30)):
        sh = m & ~np.isnan(loc)
        w2x = 100 * np.mean(np.abs(Q[m, 2] - y[m]) <= L2)
        c50 = 100 * np.mean((y[m] >= Q[m, 1]) & (y[m] <= Q[m, 3]))
        c80 = 100 * np.mean((y[m] >= Q[m, 0]) & (y[m] <= Q[m, 4]))
        lw = 100 * np.mean(np.abs(loc[sh] - y[sh]) <= L2) if sh.any() else float("nan")
        print("%-9s n=%6d  global w2x %.1f%%  50%% cov %.1f%%  80%% cov %.1f%%  |  "
              "local shown %.0f%%, w2x %.1f%%" % (name, m.sum(), w2x, c50, c80,
                                                  100 * sh.sum() / max(1, m.sum()), lw))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    X, y, grp, loc, idx = replay(read(args[1:]))
    print("turns %d, people %d" % (len(y), len(set(grp))))
    if "--louo" in sys.argv:
        louo(X, y, grp, loc, idx)
    ms, w = fit_calibrated(X, y, grp)
    out = {"version": "global-turn-1", "target": "ln seconds of a whole turn (prompt to agent done)",
           "features": list(T.FEATURES), "quantiles": list(T.QUANTILES), "widen": w,
           "trained_on": {"turns": int(len(y)), "people": int(len(set(grp)))},
           "trees": [pack(m) for m in ms]}
    with open(args[0], "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print("wrote %s, widen %s" % (args[0], w))


if __name__ == "__main__":
    main()
