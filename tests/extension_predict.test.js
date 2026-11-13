// node --test tests/  (Node 18+, no dependencies)
const test = require("node:test");
const assert = require("node:assert/strict");
const T = require("../extension/lib/predict.js");

const rec = (dur, o = {}) => Object.assign(
  { dur, ok: true, incident: false, pb: "short", cb: "small", model: "opus 5.5 medium", words: dur * 10 }, o);

test("nothing before MIN_READY good replies", () => {
  const h = Array.from({ length: T.MIN_READY - 1 }, (_, i) => rec(10 + i));
  assert.equal(T.predict(h, 50, 100, "Opus 5.5 Medium"), null);
});

test("failed, stopped-out and incident replies never count", () => {
  const h = Array.from({ length: 20 }, (_, i) => rec(10 + i, { ok: i % 2 === 0, incident: i % 3 === 0 }));
  assert.equal(T.learnedCount(h), 6);   // 10 ok, 4 of them (0,6,12,18) in an incident
});

test("band is the middle half, with a words band", () => {
  const h = Array.from({ length: 31 }, (_, i) => rec(10 + i));   // 10..40s
  const p = T.predict(h, 50, 100, "Opus 5.5 Medium", "50");
  assert.deepEqual(p.dur, { low: 17.5, high: 32.5 });
  assert.deepEqual(p.words, { low: 175, high: 325 });
  assert.equal(p.n, 31);
  assert.equal(p.learning, false);
  assert.equal(p.group, "prompt+conversation+model");
});

test("under CONFIDENT replies the band is the wide one, labelled learning", () => {
  const h = Array.from({ length: T.CONFIDENT - 1 }, (_, i) => rec(10 + i));
  const p = T.predict(h, 50, 100, "Opus 5.5 Medium", "50");
  assert.equal(p.coverage, "80");
  assert.equal(p.learning, true);
  assert.match(T.bandLine(p), /^Forecast .* · still learning$/);
});

test("eighty band is wider", () => {
  const h = Array.from({ length: 31 }, (_, i) => rec(10 + i));
  const half = T.predict(h, 50, 100, "Opus 5.5 Medium", "50");
  const eighty = T.predict(h, 50, 100, "Opus 5.5 Medium", "80");
  assert.ok(eighty.dur.low < half.dur.low && eighty.dur.high > half.dur.high);
});

test("falls back to broader groups for another model", () => {
  const h = Array.from({ length: 12 }, (_, i) => rec(10 + i));
  const p = T.predict(h, 50, 100, "Haiku 5.5", "50");
  assert.equal(p.group, "all");
});

test("model label is normalised", () => {
  assert.equal(T.modelKey("  Opus 5.5\n Medium "), "opus 5.5 medium");
  assert.equal(T.modelKey(""), null);
});

test("words band left out when old records have no word counts", () => {
  const h = Array.from({ length: 12 }, (_, i) => rec(10 + i, { words: undefined }));
  assert.equal(T.predict(h, 50, 100, "Opus 5.5 Medium").words, null);
});

test("text matches the Claude Code plugin's rounding", () => {
  assert.equal(T.duration(45), "45s");
  assert.equal(T.duration(95), "1m30s");
  assert.equal(T.duration(605), "10m");
  assert.equal(T.duration(3600 + 30 * 60), "1h30m");
  assert.equal(T.words(42), "42");
  assert.equal(T.words(347), "350");
  assert.equal(T.words(1260), "1.3k");
  assert.equal(T.words(2000), "2k");
});

test("band line", () => {
  const line = T.bandLine({ dur: { low: 20, high: 65 }, words: { low: 300, high: 800 }, n: 24, coverage: "50" });
  assert.equal(line, "Forecast 20s–1m · 300–800 words");
  const eighty = T.bandLine({ dur: { low: 5, high: 5 }, words: null, n: 10, coverage: "80" });
  assert.equal(eighty, "Forecast 5s");
});

test("live line: range then clock, then running long once past the top", () => {
  const band = { dur: { low: 20, high: 65 }, words: { low: 300, high: 800 }, n: 24, coverage: "50" };
  assert.deepEqual(T.liveLine(band, 12_000), { text: "Forecast 20s–1m · 300–800 words · 0:12", long: false });
  assert.deepEqual(T.liveLine(band, 80_000), { text: "1:20 · running long · forecast was 20s–1m", long: true });
  assert.deepEqual(T.liveLine(null, 5_000), { text: "0:05", long: false });
});

test("done line says where the reply landed", () => {
  const band = { dur: { low: 20, high: 65 } };
  assert.equal(T.doneLine(42, 450, band), "Done in 0:42, 450 words · inside the forecast");
  assert.equal(T.doneLine(90, 450, band), "Done in 1:30, 450 words · longer than the forecast (20s–1m)");
  assert.equal(T.doneLine(5, 1, band), "Done in 0:05, 1 word · quicker than the forecast (20s–1m)");
  assert.equal(T.doneLine(42, 450, null), "Done in 0:42, 450 words");
  assert.equal(T.incidentLine("Claude"), "Storm warning: Claude has a major incident. Replies may be slower.");
});

test("claude.ai incident reported, Console-only incident ignored", () => {
  const consoleOnly = { incidents: [{ name: "Console errors", status: "investigating",
    components: [{ name: "Claude Console (platform.claude.com)" }] }], components: [] };
  assert.equal(T.incidentFromSummary(consoleOnly), null);
  const web = { incidents: [{ name: "Elevated errors on claude.ai", status: "identified", impact: "major",
    components: [{ name: "claude.ai" }] }], components: [] };
  assert.equal(T.incidentFromSummary(web), "Elevated errors on claude.ai");
  const minor = { incidents: [{ name: "Slow claude.ai", status: "identified", impact: "minor",
    components: [{ name: "claude.ai" }] }], components: [] };
  assert.equal(T.incidentFromSummary(minor), null);
  const degraded = { incidents: [], components: [{ name: "claude.ai", status: "degraded_performance" }] };
  assert.equal(T.incidentFromSummary(degraded), null);
  const down = { incidents: [], components: [{ name: "claude.ai", status: "major_outage" }] };
  assert.equal(T.incidentFromSummary(down), "claude.ai: major outage");
  assert.equal(T.incidentFromSummary({ incidents: [{ name: "x", status: "resolved",
    components: [{ name: "claude.ai" }] }] }), null);
});

test("one word is singular", () => {
  assert.equal(T.wordsText(1), "1 word");
  assert.equal(T.wordsText(2), "2 words");
  assert.equal(T.wordsText(1, 1), "1 word");
  assert.equal(T.wordsText(300, 800), "300–800 words");
  assert.equal(T.bandLine({ dur: { low: 2, high: 3 }, words: { low: 1, high: 1 }, n: 10, coverage: "50" }),
    "Forecast 2s–3s · 1 word");
});

test("evaluate scores each reply only from earlier ones", () => {
  const h = Array.from({ length: 60 }, (_, i) => rec(10 + (i % 7)));
  const e = T.evaluate(h, "50");
  assert.equal(e.scored, 60 - T.MIN_READY);
  assert.equal(e.byBand["80"].scored, T.CONFIDENT - T.MIN_READY);
  assert.ok(e.coverage >= 0 && e.coverage <= 1 && e.medianRatio >= 1);
  assert.equal(T.evaluate([], "50").coverage, null);
});

test("version matches the manifest", () => {
  const m = require("../extension/manifest.json");
  assert.equal(m.version, T.VERSION);
});

test("options page names the maker with a safe outbound link", () => {
  const html = require("node:fs").readFileSync(require("node:path").join(__dirname, "../extension/options.html"), "utf8");
  const a = html.match(/<a [^>]*href="([^"]+)"[^>]*>RainDragon AI<\/a>/);
  assert.ok(a, "Made by RainDragon AI link");
  assert.ok(a[1].startsWith("https://inference.raindragon.ai/?utm_source=raindragon-forecast&utm_medium=extension"));
  assert.match(a[0], /rel="noopener"/);
});

test("each site has its own history key and status page", () => {
  const P = T.PROVIDERS;
  assert.equal(P.claude.historyKey, "history");          // unchanged: existing claude.ai history keeps working
  assert.notEqual(P.chatgpt.historyKey, P.claude.historyKey);
  assert.equal(P.chatgpt.statusUrl, "https://status.openai.com/api/v2/summary.json");
  const m = require("../extension/manifest.json");
  // every status URL is covered by a host permission (Gemini's is a path on www.google.com)
  for (const p of Object.values(P))
    assert.ok(m.host_permissions.some((h) => p.statusUrl.startsWith(h.replace(/\*$/, ""))), p.id);
});

test("ChatGPT heads-up: only a major outage of Conversations", () => {
  const C = T.PROVIDERS.chatgpt.statusComponents;
  // status.openai.com's summary.json has components only (no incidents key)
  const ok = { components: [{ name: "Conversations", status: "operational" }] };
  assert.equal(T.incidentFromSummary(ok, C), null);
  const degraded = { components: [{ name: "Conversations", status: "degraded_performance" }] };
  assert.equal(T.incidentFromSummary(degraded, C), null);
  const apiDown = { components: [{ name: "Chat Completions", status: "major_outage" }] };
  assert.equal(T.incidentFromSummary(apiDown, C), null);
  const down = { components: [{ name: "Conversations", status: "major_outage" }] };
  assert.equal(T.incidentFromSummary(down, C), "Conversations: major outage");
  const full = { components: [{ name: "Conversations", status: "full_outage" }] };
  assert.equal(T.incidentFromSummary(full, C), "Conversations: full outage");
  // and claude.ai never reacts to an OpenAI page
  assert.equal(T.incidentFromSummary(down), null);
});

// ---------- optional server comparison ----------

const prec = (dur, tok, o = {}) => rec(dur, Object.assign({ srv_tok: tok }, o));

test("server model is asked only with consent to send text, and never in off mode", () => {
  assert.equal(T.serverActive({}), false);
  assert.equal(T.serverActive({ serverMode: "shadow" }), false);
  assert.equal(T.serverActive({ serverMode: "off", serverSendText: true }), false);
  assert.equal(T.serverActive({ serverMode: "shadow", serverSendText: true }), true);
  const b = T.serverBody("iid-12345678", "x".repeat(9000));
  assert.equal(b.text.length, 8000);
  assert.equal(b.share, false);
  assert.deepEqual(Object.keys(b).sort(), ["client", "client_version", "install_id", "model", "share", "text"]);
});

test("shadow keeps showing the local band even with a good server answer", () => {
  const h = Array.from({ length: 40 }, (_, i) => prec(10 + i, 100));
  const local = T.predict(h, 50, 100, "Opus 5.5 Medium", "50");
  const pick = T.chooseBand("shadow", local, h, { tok: 900, pid: "etp_x" }, "50");
  assert.equal(pick.band, local);
  assert.equal(pick.shown, "local");
});

test("server and blend fall back to local on error, junk, or too few paired replies", () => {
  const few = Array.from({ length: 40 }, (_, i) => rec(10 + i));
  const local = T.predict(few, 50, 100, "Opus 5.5 Medium", "50");
  for (const mode of ["server", "blend"]) {
    assert.equal(T.chooseBand(mode, local, few, null, "50").band, local);                 // error/timeout
    assert.equal(T.chooseBand(mode, local, few, { tok: 300 }, "50").band, local);         // no pairs yet
  }
  assert.equal(T.serverAnswer({ detail: { code: "model_unavailable" } }), null);
  assert.equal(T.serverAnswer({ prediction_id: "etp_1", output_tokens: 0 }), null);
  assert.deepEqual(T.serverAnswer({ prediction_id: "etp_1", output_tokens: 12.4, model_version: "v" }),
                   { pid: "etp_1", tok: 12, ver: "v" });
});

test("server mode shows the server band scaled by the user's own seconds per token", () => {
  const h = Array.from({ length: 40 }, (_, i) => prec(50 + (i % 5), 100));   // ~0.5 s per token
  const local = T.predict(h, 50, 100, "Opus 5.5 Medium", "50");
  const s = T.chooseBand("server", local, h, { tok: 400 }, "50");
  assert.equal(s.shown, "server");
  assert.ok(s.band.dur.low > 190 && s.band.dur.high < 220);
  const b = T.chooseBand("blend", local, h, { tok: 400 }, "50");
  assert.equal(b.shown, "blend");
  assert.ok(b.band.dur.low > local.dur.low && b.band.dur.high < s.band.dur.high);
});

test("evaluateServer compares local, server and blend on paired replies", () => {
  const h = Array.from({ length: 30 }, (_, i) => prec(40 + i, 100, { loc: [30, 60] }));
  const e = T.evaluateServer(h, "50");
  assert.equal(e.pairedReplies, 30);
  assert.equal(e.local.scored, 30);
  assert.equal(e.server.scored, 30 - T.MIN_PAIRED);
});

// ---- the global model in the extension (RainDragonEta.forecast) ----

const TM = require("../extension/lib/turn_model.js");
const GM = { M: TM, data: require("../extension/lib/turn_model_data.js").RDTurnModelData };

test("global: a range from the first reply, marked still learning", () => {
  const fc = T.forecast([], 120, 0, "Opus 5.5 Medium", "50", "global", GM, null, 1.7e9);
  assert.equal(fc.local, null);
  assert.equal(fc.shown, "global");
  assert.ok(fc.band.dur.low > 0 && fc.band.dur.low < fc.band.dur.high);
  assert.equal(fc.band.learning, true);
  assert.match(T.bandLine(fc.band), /^Forecast .* · still learning$/);
});

test("global: the same numbers as the model on the same features", () => {
  const h = Array.from({ length: 40 }, (_, i) => rec(5 + (i % 9) * 3));
  const s = { n: 2, k: 2, done: [[1.7e9 - 90, 12], [1.7e9 - 30, 20]] };
  const fc = T.forecast(h, 150, 1000, "Opus 5.5 Medium", "50", "global", GM, s, 1.7e9);
  const hist = h.map((r) => Object.assign({}, r, { effort: r.model }));
  const f = TM.features(150, 0, "opus 5.5 medium", hist, s, 1.7e9, { pb: "short", cb: "small" });
  const want = TM.band(GM.data, f, "50");
  assert.equal(fc.shown, "global");
  assert.deepEqual(fc.band.dur, want);
  assert.equal(fc.band.learning, false);
  assert.deepEqual(fc.band.words, fc.local.words);
});

test("rangeSource local, no model, a broken model or a throwing model: the local lookup", () => {
  const h = Array.from({ length: 31 }, (_, i) => rec(10 + i));
  const local = T.predict(h, 50, 100, "Opus 5.5 Medium", "50");
  const boom = { M: Object.assign({}, TM, { features() { throw new Error("x"); } }), data: GM.data };
  for (const [src, gm] of [["local", GM], ["global", null], ["global", { M: TM, data: { trees: [] } }],
                           ["global", boom]]) {
    const fc = T.forecast(h, 50, 100, "Opus 5.5 Medium", "50", src, gm, null, 1.7e9);
    assert.equal(fc.shown, "local");
    assert.equal(fc.glb, null);
    assert.deepEqual(fc.band, local);
  }
  assert.equal(T.rangeSource({}), "global");
  assert.equal(T.rangeSource({ rangeSource: "local" }), "local");
  assert.equal(T.rangeSource({ rangeSource: "bogus" }), "global");
});

test("global switches to local once local has been closer on recent replies", () => {
  const h = Array.from({ length: 40 }, (_, i) => rec(10 + (i % 5), { loc: [9, 14], glb: [60, 200] }));
  const fc = T.forecast(h, 50, 100, "Opus 5.5 Medium", "50", "global", GM, null, 1.7e9);
  assert.equal(fc.shown, "local");
  assert.deepEqual(fc.band, fc.local);
  assert.ok(fc.glb);
  // still learning locally (under CONFIDENT): global stays
  const fc2 = T.forecast(h.slice(0, 25), 50, 100, "Opus 5.5 Medium", "50", "global", GM, null, 1.7e9);
  assert.equal(fc2.shown, "global");
});

test("in-memory sessions: prompts, finished replies, the last 10 kept", () => {
  const S = T.sessions();
  assert.deepEqual(S.get("/chat/a", 0), { n: 0, k: 0, done: [] });
  assert.equal(S.get("/chat/old", 7).n, 7);
  S.prompted("/new", 0);
  for (let i = 0; i < 12; i++) S.finished("/chat/a", 1000 + i, 5 + i);
  const s = S.get("/chat/a", 1);
  assert.equal(s.k, 12);
  assert.equal(s.done.length, 10);
  assert.deepEqual(s.done[9], [1011, 16]);
  assert.equal(s.n, 1);
});

test("chooseBand keeps the base band's name when the server is not used", () => {
  const b = { dur: { low: 1, high: 2 } };
  assert.deepEqual(T.chooseBand("shadow", b, [], null, "50", "global"), { band: b, shown: "global" });
  assert.deepEqual(T.chooseBand("shadow", b, [], null, "50"), { band: b, shown: "local" });
});
