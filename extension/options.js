const T = self.RainDragonEta;
const TM = self.RDTurnModel;
const DEFAULTS = { enabled: true, band: "50", checkStatus: true, showResult: false,
                   serverMode: "shadow", serverSendText: false, rangeSource: "global" };
const SERVER_ORIGIN = { origins: [T.SERVER_URL + "/*"] };
const $ = (id) => document.getElementById(id);
const PROVS = Object.values(T.PROVIDERS);   // claude.ai, chatgpt.com, gemini.google.com: separate histories
let state = { settings: DEFAULTS, histories: {} };

function flash(text) {
  $("saved").textContent = text;
  setTimeout(() => { $("saved").textContent = ""; }, 1500);
}

function pct(x) { return Math.round(x * 100) + "%"; }

function evalLine(h, band) {
  const good = T.learnedCount(h);
  const e = T.evaluate(h, band);
  if (!e.scored) return "not enough replies yet to check the forecast (" + good + " of " + T.MIN_READY + ").";
  const parts = Object.keys(e.byBand).sort().reverse().map((k) => {
    const b = e.byBand[k];
    return pct(b.hits / b.scored) + " of " + b.scored + " finished inside the " +
      (k === "80" ? "8-in-10 range (aim 80%)" : "middle-half range (aim 50%)");
  });
  return "how often the forecast held on your own replies: " + parts.join("; ") +
    ". Typical range: the top is " + e.medianRatio.toFixed(1) + "x the bottom.";
}

function render() {
  const s = state.settings;
  $("version").textContent = "v" + T.VERSION;
  $("enabled").checked = !!s.enabled;
  $("band").value = s.band;
  $("showResult").checked = !!s.showResult;
  $("checkStatus").checked = !!s.checkStatus;
  $("serverSendText").checked = !!s.serverSendText;
  $("serverMode").value = T.SERVER_MODES.includes(s.serverMode) ? s.serverMode : "shadow";
  $("rangeSource").value = T.rangeSource(s);
  $("count").textContent = "";
  $("eval").textContent = "";
  for (const p of PROVS) {
    const h = state.histories[p.id] || [];
    const div = document.createElement("div");
    div.textContent = p.site + ": " + h.length + " replies saved on this device (" + T.learnedCount(h) +
      " used for forecasts; durations and word counts only); " + evalLine(h, s.band);
    $("count").appendChild(div);
    const gl = TM.evaluateLive(h);
    if (gl.turns) {
      const d3 = document.createElement("div");
      const f = (b) => b.scored ? pct(b.within_2x) + " within 2x, " + pct(b.coverage) + " inside the range (" + b.scored + ")" : "not yet";
      d3.textContent = p.site + " with the shared model: " + gl.turns + " replies. Shared: " + f(gl.global) +
        ". Your own: " + f(gl.local) + ". Shown: " + f(gl.shown) + ".";
      $("count").appendChild(d3);
    }
    const sv = T.evaluateServer(h, s.band);
    if (sv.pairedReplies) {
      const d2 = document.createElement("div");
      const f = (b) => b.scored ? pct(b.within2x) + " within 2x, " + pct(b.coverage) + " inside the range (" + b.scored + ")" : "not yet";
      d2.textContent = p.site + " with a server answer: " + sv.pairedReplies + " replies. Local: " + f(sv.local) +
        ". Server: " + f(sv.server) + ". Mix: " + f(sv.blend) + ".";
      $("count").appendChild(d2);
    }
  }
}

function diagnostics() {
  const per = {};
  for (const p of PROVS) {
    const h = state.histories[p.id] || [], good = T.learnedCount(h), e = T.evaluate(h, state.settings.band);
    per[p.id] = {
      replies_recorded: h.length, replies_used: good,
      replies_failed: h.filter((r) => !r.ok).length,
      replies_in_incident: h.filter((r) => r.ok && r.incident).length,
      phase: good < T.MIN_READY ? "learning (no band yet)" : good < T.CONFIDENT ? "learning (wide band)" : "ready",
      coverage: e.coverage, scored: e.scored, median_ratio: e.medianRatio,
      server: T.evaluateServer(h, state.settings.band),
      global_model: TM.evaluateLive(h),
    };
  }
  return JSON.stringify({
    name: "raindragon-forecast-extension", version: T.VERSION, settings: state.settings,
    sites: per,
    browser: navigator.userAgent.replace(/\s*\(.*?\)\s*/g, " ").trim(),
  }, null, 2);
}

const want = { settings: DEFAULTS };
for (const p of PROVS) want[p.historyKey] = [];
chrome.storage.local.get(want, (v) => {
  const histories = {};
  for (const p of PROVS) histories[p.id] = Array.isArray(v[p.historyKey]) ? v[p.historyKey] : [];
  state = { settings: Object.assign({}, DEFAULTS, v.settings), histories };
  render();
});

function save() {
  state.settings = {
    enabled: $("enabled").checked, band: $("band").value,
    showResult: $("showResult").checked, checkStatus: $("checkStatus").checked,
    serverMode: $("serverMode").value, serverSendText: $("serverSendText").checked,
    rangeSource: $("rangeSource").value,
  };
  chrome.storage.local.set({ settings: state.settings }, () => { render(); flash("Saved"); });
}
["enabled", "band", "showResult", "checkStatus", "serverMode", "rangeSource"].forEach((id) => $(id).addEventListener("change", save));

// Sending prompt text needs the optional host permission; asked here, in the
// click, because Chrome only allows the request from a user gesture.
$("serverSendText").addEventListener("change", () => {
  if (!$("serverSendText").checked) {
    chrome.permissions.remove(SERVER_ORIGIN, () => save());
    return;
  }
  chrome.permissions.request(SERVER_ORIGIN, (granted) => {
    if (!granted) $("serverSendText").checked = false;
    save();
  });
});

$("copy").addEventListener("click", () => {
  navigator.clipboard.writeText(diagnostics()).then(() => flash("Copied"), () => flash("Copy failed"));
});

$("clear").addEventListener("click", () => {
  const empty = { chatgpt_model: null };
  for (const p of PROVS) empty[p.historyKey] = [];
  chrome.storage.local.set(empty, () => { state.histories = {}; render(); flash("Cleared"); });
});
