/* Status check for the content scripts. Runs here, not in the page, so the
 * request goes only to the provider's public status page and the chat site's
 * page never sees it. One cache per provider, CACHE_MS; every failure means
 * "no known incident". */
importScripts("lib/predict.js");

const CACHE_MS = 120 * 1000;
const TIMEOUT_MS = 3000;

const caches = {};

async function currentIncident(providerId) {
  const T = self.RainDragonEta;
  const prov = T.PROVIDERS[providerId] || T.PROVIDERS.claude;
  const now = Date.now();
  const cache = caches[prov.id];
  if (cache && now - cache.at < CACHE_MS) return cache.incident;
  let incident = null;
  try {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), TIMEOUT_MS);
    const resp = await fetch(prov.statusUrl, { signal: ctl.signal, credentials: "omit" });
    clearTimeout(t);
    if (resp.ok) {
      const body = await resp.json();
      incident = prov.statusFormat === "google"
        ? T.incidentFromGoogle(body, prov.statusProduct, Date.now())
        : T.incidentFromSummary(body, prov.statusComponents);
    }
  } catch (e) {
    incident = null;
  }
  caches[prov.id] = { at: now, incident };
  return incident;
}

// Optional server comparison (content.js, only with the user's consent to send
// the prompt text, and only once they granted the optional host permission).
// Every failure is {err}: the content script then keeps the local band.
const SERVER_TIMEOUT_MS = 5000;

async function serverPost(path, body) {
  const T = self.RainDragonEta;
  const origin = T.SERVER_URL + "/*";
  const t0 = Date.now();
  try {
    const ok = await chrome.permissions.contains({ origins: [origin] });
    if (!ok) return { err: "no_permission" };
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), SERVER_TIMEOUT_MS);
    const resp = await fetch(T.SERVER_URL + path, {
      method: "POST", body: JSON.stringify(body), signal: ctl.signal, credentials: "omit",
      headers: { "Content-Type": "application/json" },
    });
    clearTimeout(t);
    if (!resp.ok) return { err: "http_" + resp.status, ms: Date.now() - t0 };
    if (resp.status === 204) return { ok: true };
    const a = T.serverAnswer(await resp.json());
    return a ? Object.assign(a, { ms: Date.now() - t0 }) : { err: "bad_answer", ms: Date.now() - t0 };
  } catch (e) {
    return { err: e && e.name === "AbortError" ? "timeout" : "error", ms: Date.now() - t0 };
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg && msg.type === "eta_predict" && msg.body) {
    serverPost("/v1/eta/predict", msg.body).then(reply);
    return true;
  }
  if (msg && msg.type === "eta_outcome" && msg.body) {
    serverPost("/v1/eta/outcome", msg.body).then(reply);
    return true;
  }
  if (msg && msg.type === "status") {
    currentIncident(msg.provider || "claude").then((incident) => reply({ incident }));
    return true; // async reply
  }
  return false;
});
