// node --test tests/  -- gemini.google.com page markers against a SYNTHETIC
// DOM (tests/fixtures/gemini/). Nothing here was captured from the live site.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const G = require("../extension/lib/gemini_dom.js");
const T = require("../extension/lib/predict.js");
const { page } = require("./fixtures/gemini/page.js");

// Drive one turn: send at t=0, then run steps at the given times with page
// actions in between. Returns the first non-null result.
function run(pg, script) {
  const turn = G.createTurn(pg.d, 0);
  for (const [at, action] of script) {
    if (action) action(turn);
    const r = turn.step(at);
    if (r) return Object.assign({ at }, r);
  }
  return null;
}

test("streaming start/stop: ok, with visible reply words and end time", () => {
  const pg = page();
  pg.type("a synthetic prompt");
  const r = run(pg, [
    [100, () => pg.beginReply()],
    [2000, () => pg.stream("one two three")],
    [4000, () => pg.stream("four five")],
    [6000, () => pg.endReply()],
    [6500, null],
    [7100, null],
  ]);
  assert.equal(r.outcome, "ok");
  assert.equal(r.words, 5);
  assert.equal(r.dur, 6);            // ends when streaming stopped, not when settled
  assert.equal(r.first, 2);
  assert.equal(r.at, 7100);          // reported only after SETTLE_MS of no change
});

test("still streaming: nothing reported", () => {
  const pg = page();
  pg.type("x");
  const r = run(pg, [[100, () => pg.beginReply()], [5000, () => pg.stream("a b")], [60000, null]]);
  assert.equal(r, null);
});

test("thinking and hidden text are not reply words", () => {
  const pg = page();
  pg.beginReply();
  pg.thought("lots of synthetic thinking words here");
  pg.stream("the answer");
  assert.equal(G.replyWords(pg.reply), 2);
  // block boundaries separate words; inline ones do not
  const { h } = require("./fixtures/gemini/minidom.js");
  const r = h("model-response", {}, h("message-content", {}, h("p", {}, "ab"), h("p", {}, "cd", h("b", {}, "ef"))));
  assert.equal(G.replyWords(r), 2);
});

test("stopped by the user: stopped, never saved", () => {
  const pg = page();
  pg.type("x");
  const r = run(pg, [
    [100, () => pg.beginReply()],
    [2000, () => pg.stream("some words")],
    [3000, (t) => { t.stop(); pg.endReply(); }],
    [5000, null],
  ]);
  assert.equal(r.outcome, "stopped");
  assert.equal(G.toRecord(r, { pb: "short", cb: "small", model: null }, 1), null);
});

test("ok vs stopped differ only by the stop click", () => {
  const go = (stop) => {
    const pg = page();
    return run(pg, [[100, () => pg.beginReply()], [1000, () => pg.stream("w w w")],
                    [2000, (t) => { if (stop) t.stop(); pg.endReply(); }], [3100, null]]).outcome;
  };
  assert.equal(go(false), "ok");
  assert.equal(go(true), "stopped");
});

test("error marker in the new reply: failed, saved as failed with no words", () => {
  const pg = page();
  const r = run(pg, [[100, () => pg.beginReply()], [1000, () => { pg.error(); pg.endReply(); }], [1200, null]]);
  assert.equal(r.outcome, "failed");
  const rec = G.toRecord(r, { pb: "short", cb: "small", model: "2.5 flash", incident: false }, 1);
  assert.equal(rec.ok, false);
  assert.equal(rec.words, undefined);
});

test("markers missing (no stop button, no aria-busy): unknown, nothing saved", () => {
  const pg = page({ markers: false });
  const r = run(pg, [
    [100, () => pg.beginReply()],
    [1000, () => pg.stream("a reply that nobody marked as streaming")],
    [3000, null],
    [7000, null],
  ]);
  assert.equal(r.outcome, "unknown");
  assert.equal(G.toRecord(r, {}, 1), null);
});

test("no reply element ever appears: unknown", () => {
  const pg = page({ markers: false });
  const r = run(pg, [[1000, null], [G.NO_REPLY_MS + 1, null]]);
  assert.equal(r.outcome, "unknown");
});

test("streaming ended but the reply is empty: unknown", () => {
  const pg = page();
  const r = run(pg, [[100, () => pg.beginReply()], [1000, () => pg.endReply()], [2100, null]]);
  assert.equal(r.outcome, "unknown");
});

test("an unrecognisable page reads as empty, not as an error", () => {
  const { doc, h } = require("./fixtures/gemini/minidom.js");
  const d = doc(h("div", {}, "some other page"));
  assert.equal(G.promptChars(d), 0);
  assert.equal(G.modelLabel(d), "");
  assert.equal(G.composer(d), null);
  assert.equal(G.lastReply(d), null);
});

test("model picker label is the first similar level; missing = one bucket", () => {
  const pg = page({ model: "2.5  Pro" });
  assert.equal(T.modelKey(G.modelLabel(pg.d)), "2.5 pro");
  assert.equal(T.modelKey(G.modelLabel(page({ model: null }).d)), null);
  pg.type("12345");
  assert.equal(G.promptChars(pg.d), 5);
  assert.equal(G.composer(pg.d).tagName, "INPUT-AREA-V2");
  assert.ok(G.contextChars(pg.d) > 0);
});

// ---- the content script end to end, with fake chrome storage and clock ----

function contentScript(pg, stored = {}) {
  const store = Object.assign({}, stored);
  const listeners = {};
  const ticks = [];
  let now = 1_000_000;
  const shown = [];
  const ctx = {
    console, Object, Array, Math, JSON, String, Number, Map,
    location: { pathname: "/app/synthetic" },
    Date: class extends Date { static now() { return now; } },
    setInterval: (fn, ms) => { if (ms < 1000) ticks.push(fn); },
    document: Object.assign(pg.d, { addEventListener: (k, fn) => { listeners[k] = fn; } }),
    chrome: {
      storage: {
        local: {
          get: (want, cb) => { const out = {}; for (const k of Object.keys(want)) out[k] = k in store ? store[k] : want[k]; cb(out); },
          set: (v) => Object.assign(store, JSON.parse(JSON.stringify(v))),
        },
        onChanged: { addListener: () => {} },
      },
      runtime: { sendMessage: (m, cb) => cb({ incident: null }), lastError: null },
    },
    RainDragonEtaPill: { create: () => ({ show: (box, text, warn, long) => shown.push({ box, text, warn, long }) }),
                         clock: (ms) => String(Math.floor(ms / 1000)) },
  };
  ctx.self = ctx;
  vm.createContext(ctx);
  const src = (f) => fs.readFileSync(path.join(__dirname, "..", "extension", f), "utf8");
  vm.runInContext(src("lib/predict.js"), ctx);
  vm.runInContext(src("lib/turn_model.js"), ctx);
  vm.runInContext(src("lib/turn_model_data.js"), ctx);
  vm.runInContext(src("lib/gemini_dom.js"), ctx);
  vm.runInContext(src("gemini.js"), ctx);
  return {
    store, shown,
    advance(ms) { now += ms; ticks.forEach((f) => f()); },
    enter() { listeners.keydown({ key: "Enter", shiftKey: false, isComposing: false, target: pg.editor }); },
    click(el) { listeners.pointerdown({ target: el }); },
  };
}

function oneTurn(cs, pg, { stop = false } = {}) {
  pg.type("a synthetic prompt");
  cs.enter();
  pg.beginReply(); cs.advance(500);
  pg.stream("one two three four"); cs.advance(3000);
  if (stop) cs.click(pg.stopButton);
  pg.endReply(); cs.advance(250);
  cs.advance(1200);
}

test("Gemini replies go to their own history; claude.ai and chatgpt.com are untouched", () => {
  const claude = [{ dur: 5, ok: true, incident: false, pb: "short", cb: "small", model: "opus", words: 9 }];
  const pg = page({ model: "2.5 Flash" });
  const cs = contentScript(pg, { history: claude, history_chatgpt: [] });
  oneTurn(cs, pg);
  assert.equal(cs.store.history_gemini.length, 1);
  const rec = cs.store.history_gemini[0];
  assert.equal(rec.ok, true);
  assert.equal(rec.words, 4);
  assert.equal(rec.model, "2.5 flash");
  assert.equal(rec.pb, "short");
  assert.ok(Math.abs(rec.dur - 3.5) < 0.3, String(rec.dur));
  assert.deepEqual(cs.store.history, claude);
  assert.deepEqual(cs.store.history_chatgpt, []);
  // record holds numbers and buckets only; the first reply had the global range only
  assert.deepEqual(Object.keys(rec).sort(),
                   ["cb", "dur", "first", "glb", "incident", "model", "ok", "pb", "shown", "t", "words"]);
  assert.equal(rec.shown, "global");
  assert.ok(rec.glb[0] > 0 && rec.glb[0] < rec.glb[1]);
});

test("a stopped Gemini reply is not saved", () => {
  const pg = page();
  const cs = contentScript(pg);
  oneTurn(cs, pg, { stop: true });
  assert.equal((cs.store.history_gemini || []).length, 0);
});

test("first run on Gemini starts from zero even with a long Claude history", () => {
  const claude = Array.from({ length: 40 }, (_, i) =>
    ({ dur: 10 + i, ok: true, incident: false, pb: "short", cb: "small", model: null, words: 50 }));
  const pg = page({ model: null });
  const cs = contentScript(pg, { history: claude, settings: { rangeSource: "local" } });
  pg.type("hello");
  cs.advance(250);
  assert.match(cs.shown[cs.shown.length - 1].text, /^Forecast is learning your pace\. .* \(0 so far\)\.$/);
  assert.equal(cs.shown[cs.shown.length - 1].box, pg.composer);
});

test("first run on Gemini with the global model: a range marked still learning", () => {
  const pg = page({ model: null });
  const cs = contentScript(pg, {});
  pg.type("hello");
  cs.advance(250);
  assert.match(cs.shown[cs.shown.length - 1].text, /^Forecast \S+ · still learning$/);
});

test("PROVIDERS: Gemini has its own key and the Google status host is permitted", () => {
  const P = T.PROVIDERS.gemini;
  assert.equal(P.historyKey, "history_gemini");
  const m = require("../extension/manifest.json");
  assert.ok(m.host_permissions.includes("https://www.google.com/appsstatus/*"));
  const cs = m.content_scripts.find((c) => c.matches.includes("https://gemini.google.com/*"));
  assert.deepEqual(cs.js, ["lib/predict.js", "lib/turn_model.js", "lib/turn_model_data.js", "lib/pill.js",
                           "lib/gemini_dom.js", "gemini.js"]);
});

// ---- Google status dashboard (synthetic incidents; real field names) ----

test("Gemini heads-up: active major incidents on the Gemini product only", () => {
  const ID = T.PROVIDERS.gemini.statusProduct;
  const now = Date.parse("2026-10-19T12:00:00Z");
  const inc = (o) => Object.assign({ begin: "2026-10-19T11:00:00+00:00", end: null, severity: "low",
    status_impact: "SERVICE_INFORMATION", service_key: ID, affected_products: [{ title: "Gemini", id: ID }] }, o);
  const G2 = (list) => T.incidentFromGoogle(list, ID, now);
  assert.equal(G2([inc({ status_impact: "SERVICE_OUTAGE" })]), "service outage");
  assert.equal(G2([inc({ severity: "high", status_impact: "SERVICE_DISRUPTION" })]), "high-severity incident");
  const noEnd = inc({ status_impact: "SERVICE_OUTAGE" }); delete noEnd.end;
  assert.equal(G2([noEnd]), "service outage");                                     // end missing
  assert.equal(G2([inc({ status_impact: "SERVICE_OUTAGE", end: "" })]), "service outage");
  // not major
  assert.equal(G2([inc({ severity: "medium", status_impact: "SERVICE_DISRUPTION" })]), null);
  // ended
  assert.equal(G2([inc({ status_impact: "SERVICE_OUTAGE", end: "2026-10-19T11:30:00+00:00" })]), null);
  // another product (e.g. Gmail)
  assert.equal(G2([inc({ status_impact: "SERVICE_OUTAGE", service_key: "other", affected_products: [{ id: "other" }] })]), null);
  // garbage in: no incident, no throw
  for (const bad of [null, {}, "x", [null, 1, "s"], [inc({ status_impact: "SERVICE_OUTAGE", end: "not a date" })]])
    assert.equal(G2(bad), null);
});
