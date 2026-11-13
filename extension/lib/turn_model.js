/* The global turn model (plugin/scripts/raindragon_eta/turn_model.py) in plain JS:
 * the same feature vector and the same LightGBM tree walk over turn_model.json.
 *
 * Not used by the extension yet: it was trained on coding-agent turns, and the
 * extension times chat replies, which it was never measured on. It is here so a
 * JS host can use the one file, and tests/turn_model.test.js pins it to the
 * Python numbers.
 *
 * Plain script: loaded by node for tests.
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

  // session = {n, k, done: [[end, dur], ...]}; history = finished turns, oldest first
  function features(promptChars, transcriptBytes, effort, history, session, now) {
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
    const b = localBand(good, promptBucket(promptChars || 0), contextBucket(transcriptBytes || 0),
                        effort == null ? null : effort);
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

  const api = { QUANTILES, features, quantiles, band, treeSum };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.RDTurnModel = api;
})(typeof self !== "undefined" ? self : this);
