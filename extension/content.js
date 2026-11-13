/* RainDragon Forecast on claude.ai.
 *
 * While you type: a band from your own similar past replies, for example
 * "Half of your similar replies took 20s–1m, 300–800 words (from 24)".
 * While Claude replies: the elapsed time next to that band.
 * After: the reply's duration and word count are saved locally (no text).
 *
 * Optional (settings serverSendText + serverMode, default off / shadow): the
 * prompt text goes to RainDragon's server model via background.js; in shadow
 * mode its answer is only logged next to the local band (numbers only).
 *
 * Page signals used (claude.ai test ids, checked 2026-10-08):
 *   [data-testid=chat-input]           the prompt editor
 *   [data-testid=chat-input-send]      the send button
 *   [data-testid=model-selector-dropdown]  model + effort label
 *   [data-testid=transcript-list]      the conversation
 *   [data-testid=assistant-message][data-is-streaming=true|false]
 *   [data-testid=failed-send-status-region]
 * If claude.ai renames these, the pill simply stops appearing: nothing on the
 * page is changed or blocked.
 */
(function () {
  "use strict";
  const T = self.RainDragonEta;
  const SEL = {
    input: '[data-testid="chat-input"]',
    send: '[data-testid="chat-input-send"]',
    model: '[data-testid="model-selector-dropdown"]',
    transcript: '[data-testid="transcript-list"]',
    reply: '[data-testid="assistant-message"]',
    failed: '[data-testid="failed-send-status-region"]',
  };
  const TICK_MS = 250;
  const MAX_TURN_MS = 30 * 60 * 1000;   // longer than this is a tab left open
  const HISTORY_CAP = 1000;
  const DEFAULTS = { enabled: true, band: "50", checkStatus: true, showResult: false,
                     serverMode: "shadow", serverSendText: false, rangeSource: "global" };
  const GM = { M: self.RDTurnModel, data: self.RDTurnModelData };
  const SESS = T.sessions();

  let settings = Object.assign({}, DEFAULTS);
  let history = [];
  let pending = null;     // the reply in progress
  let lastResult = null;  // {text, until}
  let incident = null;
  let ctxCache = { at: 0, chars: 0 };
  let installId = null;

  // ---------- storage ----------

  function load() {
    chrome.storage.local.get({ history: [], settings: DEFAULTS, install_id: null }, (v) => {
      history = Array.isArray(v.history) ? v.history : [];
      settings = Object.assign({}, DEFAULTS, v.settings || {});
      installId = typeof v.install_id === "string" ? v.install_id : null;
      if (!installId) {
        installId = crypto.randomUUID();
        try { chrome.storage.local.set({ install_id: installId }); } catch (e) { /* reloaded */ }
      }
    });
  }
  chrome.storage.onChanged.addListener((ch, area) => {
    if (area !== "local") return;
    if (ch.settings) settings = Object.assign({}, DEFAULTS, ch.settings.newValue || {});
    if (ch.history) history = Array.isArray(ch.history.newValue) ? ch.history.newValue : [];
  });

  function save(rec) {
    history = history.concat([rec]).slice(-HISTORY_CAP);
    try { chrome.storage.local.set({ history }); } catch (e) { /* extension reloaded */ }
  }

  function refreshStatus() {
    if (!settings.checkStatus) { incident = null; return; }
    try {
      chrome.runtime.sendMessage({ type: "status", provider: "claude" }, (r) => {
        incident = chrome.runtime.lastError ? null : (r && r.incident) || null;
      });
    } catch (e) { incident = null; }
  }

  // ---------- page reading ----------

  const $ = (s) => document.querySelector(s);

  function promptText() {
    const el = $(SEL.input);
    return el ? el.innerText.trim() : "";
  }

  function promptChars() {
    return promptText().length;
  }

  // Shadow / server / blend: ask the server model. The text goes only to
  // background.js (and from there to the server); nothing here keeps it.
  function askServer(p, text) {
    if (!T.serverActive(settings) || !installId || !text) return;
    try {
      chrome.runtime.sendMessage({ type: "eta_predict", body: T.serverBody(installId, text) }, (r) => {
        if (chrome.runtime.lastError || !r || pending !== p) return;
        p.srv = r;
        const pick = T.chooseBand(settings.serverMode, p.baseBand, history, r.tok ? r : null, settings.band,
                                  p.baseShown);
        p.band = pick.band;
        p.shown = pick.shown;
      });
    } catch (e) { /* extension reloaded: local band stays */ }
  }

  function contextChars(fresh) {
    const now = Date.now();
    if (fresh || now - ctxCache.at > 2000) {
      const el = $(SEL.transcript);
      ctxCache = { at: now, chars: el ? el.innerText.length : 0 };
    }
    return ctxCache.chars;
  }

  function modelLabel() {
    const el = $(SEL.model);
    return el ? el.innerText : "";
  }

  // This conversation's in-memory session, and how many replies it already shows.
  function sessionKey() { return location.pathname; }
  function priorReplies() { return document.querySelectorAll(SEL.reply).length; }

  function forecast(pc, cc, model, nowMs) {
    return T.forecast(history, pc, cc, model, settings.band, T.rangeSource(settings), GM,
                      SESS.get(sessionKey(), priorReplies()), nowMs / 1000);
  }

  function lastReply() {
    const all = document.querySelectorAll(SEL.reply);
    return all.length ? all[all.length - 1] : null;
  }

  function countWords(text) {
    const m = (text || "").match(/\S+/g);
    return m ? m.length : 0;
  }

  // Each reply carries a screen-reader-only heading ("Claude responded: ..."):
  // its words are not part of the reply.
  function wordCount(el) {
    if (!el) return 0;
    let n = countWords(el.innerText);
    for (const sr of el.querySelectorAll(".sr-only")) n -= countWords(sr.innerText);
    return Math.max(0, n);
  }

  // ---------- turn tracking ----------

  function onSend() {
    if (!settings.enabled) return;     // kill switch: show nothing, record nothing
    const now = Date.now();
    if (pending && now - pending.start < 1500) return;   // Enter + click for one send
    const text = promptText();
    const pc = text.length;
    if (pc === 0) return;
    const cc = contextChars(true);
    const model = modelLabel();
    const fc = forecast(pc, cc, model, now);
    SESS.prompted(sessionKey(), priorReplies());
    pending = {
      start: now, before: lastReply(), el: null, sawStreaming: false, firstAt: null,
      pb: T.promptBucket(pc), cb: T.contextBucket(cc), model: T.modelKey(model),
      incident: !!incident, stopped: false,
      band: fc.band, localBand: fc.local, glbBand: fc.glb, baseBand: fc.band, baseShown: fc.shown,
      shown: fc.shown, srv: null,
    };
    lastResult = null;
    askServer(pending, text);
  }

  function finish(ok) {
    const p = pending;
    pending = null;
    if (!p || p.stopped) return;           // stopped by the user: not a real duration
    const dur = (Date.now() - p.start) / 1000;
    if (dur <= 0 || dur * 1000 > MAX_TURN_MS) return;
    const rec = { t: Math.floor(Date.now() / 1000), dur: Math.round(dur * 100) / 100,
                  pb: p.pb, cb: p.cb, model: p.model, ok, incident: p.incident };
    if (ok) {
      rec.words = wordCount(p.el);
      if (p.firstAt) rec.first = Math.round((p.firstAt - p.start) / 10) / 100;
    }
    if (p.localBand) rec.loc = T.pair(p.localBand);
    if (p.glbBand) rec.glb = T.pair(p.glbBand);
    if (p.shown !== "local" || p.glbBand) rec.shown = p.shown;
    if (p.srv && p.srv.tok) {
      rec.srv_tok = p.srv.tok; rec.srv_ms = p.srv.ms; rec.srv_ver = p.srv.ver;
      if (T.serverActive(settings) && installId) {
        try {
          chrome.runtime.sendMessage({ type: "eta_outcome", body: {
            prediction_id: p.srv.pid, install_id: installId, duration_ms: Math.round(dur * 1000), failed: !ok } },
            () => void chrome.runtime.lastError);
        } catch (e) { /* reloaded */ }
      }
    } else if (p.srv && p.srv.err) {
      rec.srv_err = String(p.srv.err).slice(0, 20);
    }
    save(rec);
    SESS.finished(sessionKey(), Date.now() / 1000, dur);
    if (ok && settings.showResult) {
      const text = T.doneLine(dur, rec.words, p.band);
      lastResult = { text, until: Date.now() + 15000 };
    }
  }

  function track() {
    const p = pending;
    if (!p) return;
    if (Date.now() - p.start > MAX_TURN_MS) { pending = null; return; }
    const failed = $(SEL.failed);
    if (failed && failed.innerText.trim()) { finish(false); return; }
    const el = lastReply();
    if (!el || el === p.before && el.getAttribute("data-is-streaming") !== "true") return;
    p.el = el;
    const streaming = el.getAttribute("data-is-streaming") === "true";
    if (!p.firstAt && wordCount(el) > 0) p.firstAt = Date.now();
    if (streaming) { p.sawStreaming = true; return; }
    if (p.sawStreaming || wordCount(el) > 0) finish(true);
  }

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.shiftKey || e.isComposing) return;
    if (e.target && e.target.closest && e.target.closest(SEL.input)) onSend();
  }, true);

  document.addEventListener("pointerdown", (e) => {
    const t = e.target && e.target.closest ? e.target : null;
    if (!t) return;
    if (t.closest(SEL.send)) { onSend(); return; }
    const b = t.closest("button[aria-label]");
    if (pending && b && /^stop/i.test(b.getAttribute("aria-label"))) pending.stopped = true;
  }, true);

  // ---------- the pill ----------

  const PILL = self.RainDragonEtaPill;
  const pill = PILL.create();

  function message() {
    const warn = incident ? T.incidentLine("Claude") : "";
    if (pending) {
      const live = T.liveLine(pending.band, Date.now() - pending.start);
      return { text: live.text, warn, long: live.long };
    }
    if (lastResult && Date.now() < lastResult.until) return { text: lastResult.text, warn };
    const pc = promptChars();
    if (pc === 0) return { text: "", warn };
    const b = forecast(pc, contextChars(false), modelLabel(), Date.now()).band;
    if (b) return { text: T.bandLine(b), warn };
    return { text: T.learningLine(T.learnedCount(history)), warn };
  }

  function render() {
    const input = $(SEL.input);
    const box = input && (input.closest("fieldset") || input);
    const m = settings.enabled ? message() : { text: "", warn: "" };
    pill.show(box, m.text, m.warn, m.long);
  }

  load();
  refreshStatus();
  setInterval(refreshStatus, 120 * 1000);
  setInterval(() => {
    try { track(); render(); } catch (e) { /* never break the page */ }
  }, TICK_MS);
})();
