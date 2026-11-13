// node --test tests/  (Node 18+, no dependencies)
// The JS twin of the global turn model gives the Python numbers
// (tests/fixtures/turn_model/cases.json, written by tools/turn_model_fixture.py).
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const T = require("../extension/lib/turn_model.js");

const model = JSON.parse(fs.readFileSync(
  path.join(__dirname, "..", "plugin", "scripts", "raindragon_eta", "turn_model.json"), "utf8"));
const fx = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures", "turn_model", "cases.json"), "utf8"));

const close = (a, b, tol) => assert.ok(Math.abs(a - b) <= tol, `${a} vs ${b}`);

test("fixture was made from the shipped model", () => {
  assert.equal(fx.model_version, model.version);
});

test("same features as the Python model", () => {
  for (const c of fx.cases) {
    const f = T.features(c.prompt_chars, c.transcript_bytes, c.effort, c.history, c.session, c.now);
    for (const [k, want] of Object.entries(c.features)) {
      if (want === null) assert.ok(Number.isNaN(f[k]), `${k} should be missing`);
      else close(f[k], want, 1e-9);
    }
  }
});

test("same calibrated quantiles as the Python model", () => {
  for (const c of fx.cases) {
    const f = T.features(c.prompt_chars, c.transcript_bytes, c.effort, c.history, c.session, c.now);
    const q = T.quantiles(model, f);
    q.forEach((v, i) => close(v, c.quantiles[i], 1e-9));
  }
});

test("a band from the first turn, the 80% one around the 50% one", () => {
  const f = T.features(120, 0, null, [], null, 1000);
  const b50 = T.band(model, f, "50");
  const b80 = T.band(model, f, "80");
  assert.ok(b50.low > 0 && b50.low < b50.high);
  assert.ok(b80.low <= b50.low && b80.high >= b50.high);
});

test("no model, no band", () => {
  assert.equal(T.band(null, {}, "50"), null);
});

test("the extension ships the plugin's model, unchanged", () => {
  const bundled = require("../extension/lib/turn_model_data.js").RDTurnModelData;
  assert.deepEqual(bundled, model);
  assert.equal(T.load(bundled), bundled);
});

test("load refuses a model of the wrong shape", () => {
  assert.equal(T.load(null), null);
  assert.equal(T.load({}), null);
  assert.equal(T.load(Object.assign({}, model, { features: model.features.concat(["prompt_text"]) })), null);
  assert.equal(T.load(Object.assign({}, model, { quantiles: [0.5] })), null);
});

test("buckets passed in (the extension's sizes): same features and quantiles as Python", () => {
  assert.ok(fx.bucket_cases.length >= 30);
  for (const c of fx.bucket_cases) {
    const f = T.features(c.prompt_chars, 0, c.effort, c.history, c.session, c.now, { pb: c.pb, cb: c.cb });
    for (const [k, want] of Object.entries(c.features)) {
      if (want === null) assert.ok(Number.isNaN(f[k]), `${k} should be missing`);
      else close(f[k], want, 1e-9);
    }
    T.quantiles(model, f).forEach((v, i) => close(v, c.quantiles[i], 1e-9));
  }
});

test("same global/local pick and live scores as Python", () => {
  for (const c of fx.choose_cases) {
    assert.equal(T.localIsBetter(c.history), c.local_is_better);
    const glb = c.global ? { tag: "global" } : null;
    const local = c.local ? { tag: "local", learning: c.local.learning } : null;
    assert.equal(T.choose(glb, local, c.history)[1], c.shown);
    const got = T.evaluateLive(c.history), want = c.evaluate_live;
    assert.equal(got.turns, want.turns);
    for (const k of ["global", "local", "shown"]) {
      assert.equal(got[k].scored, want[k].scored);
      for (const m of ["coverage", "within_2x"]) {
        if (want[k][m] === null) assert.equal(got[k][m], null);
        else close(got[k][m], want[k][m], 1e-12);
      }
    }
  }
});
