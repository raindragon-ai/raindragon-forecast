/* The global turn model (plugin/scripts/raindragon_eta/turn_model.py) in plain JS:
 * the same feature vector, the same LightGBM tree walk over turn_model.json and
 * the same global/local pick (choose). tests/turn_model.test.js pins it to the
 * Python numbers.
 *
 * The extension uses it through RainDragonEta.forecast (predict.js), with the
 * model bundled as lib/turn_model_data.js. It was trained on coding-agent turns;
 * the extension times chat replies, which it was never measured on, so the pick
 * rule (local once it has been closer for you) matters more there.
 *
 * Plain script: loaded by the content scripts, and by node for tests.
 */
(function (root) {
  "use strict";

  const QUANTILES = [0.1, 0.25, 0.5, 0.75, 0.9];
  const SESSION_KEEP = 10;
  const USER_MIN = 3;
  const RECENT = 300;
  const MIN_READY = 10;
  const CONFIDENT = 30;
  const MIN_GROUP = 8;

  function median(v) {
    const s = v.slice().sort((a, b) => a - b);
    const n = s.length;
    return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2;
  }

  function quantile(sorted, q) {
    const n = sorted.length;
    if (n === 1) return sorted[0];
    const pos = q * (n - 1);
    const lo = Math.floor(pos);
    const hi = Math.min(lo + 1, n - 1);
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
  }

  function promptBucket(c) { return c < 200 ? "short" : c < 2000 ? "medium" : "long"; }
  function contextBucket(b) { return b < 200000 ? "small" : b < 1000000 ? "medium" : "large"; }

  function usable(history) {
    return (history || []).filter(
      (r) => r && r.ok && !r.incident && typeof r.dur === "number" && r.dur > 0
    ).slice(-RECENT);
  }

  // predict._predict_buckets: the local band, [low, high] in seconds, or null
  function localBand(good, pb, cb, effort) {
    if (good.length < MIN_READY) return null;
    const cov = good.length < CONFIDENT ? [0.1, 0.9] : [0.25, 0.75];
    const groups = [
      (r) => r.pb === pb && r.cb === cb && r.effort === effort,
      (r) => r.pb === pb && r.effort === effort,
      (r) => r.effort === effort,
      () => true,
    ];
    for (let i = 0; i < groups.length; i++) {
      const d = good.filter(groups[i]).map((r) => r.dur).sort((a, b) => a - b);
      if (d.length >= MIN_GROUP || i === groups.length - 1) {
        return [quantile(d, cov[0]), quantile(d, cov[1])];
      }
    }
    return null;
  }

  // session = {n, k, done: [[end, dur], ...]}; history = finished turns, oldest first.
  // buckets: {pb, cb} for hosts that bucket sizes their own way (the extension).
  function features(promptChars, transcriptBytes, effort, history, session, now, buckets) {
    const f = { lp: Math.log1p(Math.max(0, promptChars || 0)) };
    const s = session || {};
    const kept = (s.done || []).filter((d) => Array.isArray(d) && d.length === 2 && d[1] > 0);
    const done = kept.filter((d) => d[0] <= now);
    f.lprior = Math.log1p(Math.max(0, s.n || 0));
    f.s_n = Math.max(done.length, (s.k || 0) - (kept.length - done.length));
    f.s_prev1 = f.s_prev2 = f.s_med = f.s_gap = NaN;
    if (done.length) {
      f.s_prev1 = Math.log(done[done.length - 1][1]);
      if (done.length > 1) f.s_prev2 = Math.log(done[done.length - 2][1]);
      f.s_med = Math.log(median(done.slice(-SESSION_KEEP).map((d) => d[1])));
      f.s_gap = Math.log1p(Math.max(0, now - done[done.length - 1][0]));
    }
    const good = usable(history);
    for (const k of ["u_med", "u_q25", "u_q75", "u_q10", "u_q90", "u_med5", "loc_lo", "loc_hi"]) f[k] = NaN;
    f.u_n = good.length;
    if (good.length >= USER_MIN) {
      const lh = good.map((r) => Math.log(r.dur));
      const s2 = lh.slice().sort((a, b) => a - b);
      f.u_med = median(lh);
      f.u_q25 = quantile(s2, 0.25); f.u_q75 = quantile(s2, 0.75);
      f.u_q10 = quantile(s2, 0.10); f.u_q90 = quantile(s2, 0.90);
      f.u_med5 = median(lh.slice(-5));
    }
    const pb = buckets ? buckets.pb : promptBucket(promptChars || 0);
    const cb = buckets ? buckets.cb : contextBucket(transcriptBytes || 0);
    const b = localBand(good, pb, cb, effort == null ? null : effort);
    if (b && b[0] > 0 && b[1] > 0) { f.loc_lo = Math.log(b[0]); f.loc_hi = Math.log(b[1]); }
    return f;
  }

  function treeSum(t, x) {
    let total = 0;
    for (let node of t.roots) {
      while (node >= 0) {
        const v = x[t.feat[node]];
        const nan = Number.isNaN(v);
        const m = t.miss[node];
        const v0 = nan && m !== 2 ? 0 : v;
        let left;
        if ((m === 1 && Math.abs(v0) <= 1e-35) || (m === 2 && nan)) left = !!t.dleft[node];
        else left = v0 <= t.thr[node];
        node = left ? t.left[node] : t.right[node];
      }
      total += t.leaf[~node];
    }
    return total;
  }

  // ln(seconds) at QUANTILES, calibrated and non-crossing
  function quantiles(model, f) {
    const x = model.features.map((n) => (n in f ? f[n] : NaN));
    const q = model.trees.map((t) => treeSum(t, x)).sort((a, b) => a - b);
    const w50 = model.widen["50"], w80 = model.widen["80"];
    q[1] -= w50; q[3] += w50;
    q[0] = Math.min(q[0] - w80, q[1]);
    q[4] = Math.max(q[4] + w80, q[3]);
    return q;
  }

  // {low, high} in seconds for coverage "50" or "80", or null without a model
  function band(model, f, coverage) {
    if (!model) return null;
    const q = quantiles(model, f);
    return coverage === "80" ? { low: Math.exp(q[0]), high: Math.exp(q[4]) }
                             : { low: Math.exp(q[1]), high: Math.exp(q[3]) };
  }

  const FEATURES = ["lp", "lprior", "s_n", "s_prev1", "s_prev2", "s_med", "s_gap",
                    "u_n", "u_med", "u_q25", "u_q75", "u_q10", "u_q90", "u_med5", "loc_lo", "loc_hi"];

  // turn_model.load: the model if it has the expected shape, else null
  function load(raw) {
    try {
      if (!raw || raw.quantiles.length !== QUANTILES.length ||
          raw.quantiles.some((q, i) => Number(q) !== QUANTILES[i])) return null;
      if (raw.features.some((n) => FEATURES.indexOf(n) < 0)) return null;
      if (!Array.isArray(raw.trees) || raw.trees.length !== QUANTILES.length) return null;
      if (!(typeof raw.widen["50"] === "number" && typeof raw.widen["80"] === "number")) return null;
      return raw;
    } catch (e) {
      return null;
    }
  }

  // ---- which band to show (turn_model.choose) ----

  const PICK_WINDOW = 50;
  const PICK_MIN = 20;

  function hit(b, dur) {
    return Array.isArray(b) && b.length === 2 && b.every((v) => typeof v === "number" && v > 0) &&
      Math.abs(Math.log(Math.sqrt(b[0] * b[1]) / dur)) <= Math.log(2);
  }

  function localIsBetter(history) {
    const both = usable(history).filter((r) => r.glb && r.loc).slice(-PICK_WINDOW);
    if (both.length < PICK_MIN) return false;
    let loc = 0, glb = 0;
    for (const r of both) { if (hit(r.loc, r.dur)) loc++; if (hit(r.glb, r.dur)) glb++; }
    return loc > glb;
  }

  // [band, "global" | "local"]; local: an object with a `learning` flag, or null
  function choose(glb, local, history) {
    if (glb == null) return [local, "local"];
    if (local != null && !local.learning && localIsBetter(history)) return [local, "local"];
    return [glb, "global"];
  }

  // turn_model.evaluate_live, on replies that recorded the global band
  function evaluateLive(history) {
    const rows = usable(history).filter((r) => r.glb);
    const shown = (r) => (r.shown === "local" && r.loc ? r.loc : r.glb);
    const out = { turns: rows.length };
    for (const [name, pick] of [["global", (r) => r.glb], ["local", (r) => r.loc], ["shown", shown]]) {
      const got = rows.filter((r) => pick(r)).map((r) => [pick(r), r.dur]);
      const n = got.length;
      out[name] = {
        scored: n,
        coverage: n ? got.filter(([b, d]) => b[0] <= d && d <= b[1]).length / n : null,
        within_2x: n ? got.filter(([b, d]) => hit(b, d)).length / n : null,
      };
    }
    return out;
  }

  const api = { QUANTILES, FEATURES, PICK_WINDOW, PICK_MIN, features, quantiles, band, treeSum, load,
                localIsBetter, choose, evaluateLive };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.RDTurnModel = api;
})(typeof self !== "undefined" ? self : this);
