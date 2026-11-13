/* RainDragon Forecast: the small pill shown just above a chat site's message box.
 * Shared by the claude.ai and chatgpt.com content scripts. It lives in a closed
 * shadow root, ignores the pointer, and never changes the page's own elements.
 * It starts with the RainDragon dragon (icons/mark.png: the logo without its
 * wordmark, which is unreadable at 20px), then the forecast.
 *
 * It must never cover the page's own menus: its z-index sits below the usual
 * popover layers (Tailwind z-50, Angular CDK 1000), and it hides while any open
 * menu, listbox, popover or dialog overlaps it, or a modal is open. It is kept
 * inside the viewport. */
(function (root) {
  "use strict";

  const Z_INDEX = 30;
  const GAP = 6;
  const MARGIN = 8;
  const OVERLAYS = [
    '[role="menu"]', '[role="listbox"]', '[role="dialog"]', '[role="alertdialog"]',
    '[role="tooltip"]', "[data-radix-popper-content-wrapper]", "[popover]", "dialog[open]",
    ".cdk-overlay-pane", '[aria-modal="true"]',
  ].join(",");

  function clock(ms) {
    const s = Math.floor(ms / 1000);
    return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
  }

  function create() {
    const host = document.createElement("div");
    host.id = "raindragon-forecast-host";
    const shadow = host.attachShadow({ mode: "closed" });
    shadow.innerHTML =
      '<style>' +
      ':host{all:initial}' +
      '.pill{position:fixed;z-index:' + Z_INDEX + ';max-width:min(640px,calc(100vw - ' + 2 * MARGIN + 'px));' +
      'box-sizing:border-box;' +
      'font:12px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif;' +
      'padding:3px 10px;border-radius:999px;pointer-events:none;' +
      'background:rgba(240,238,230,.95);color:#3d3929;border:1px solid rgba(0,0,0,.08);' +
      'white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
      '.logo{width:20px;height:20px;vertical-align:-5px;margin-right:6px}b{font-weight:600}' +
      '.warn{color:#b91c1c}.long{color:#a16207}' +
      '@media (prefers-color-scheme:dark){.pill{background:rgba(48,48,46,.95);color:#e8e6dc;' +
      'border-color:rgba(255,255,255,.1)}.warn{color:#fca5a5}.long{color:#fcd34d}}' +
      '</style><div class="pill" hidden></div>';
    const pill = shadow.querySelector(".pill");

    function logo() {
      const img = document.createElement("img");
      img.className = "logo";
      img.alt = "RainDragon Forecast";
      try { img.src = chrome.runtime.getURL("icons/mark.png"); } catch (e) { return null; }
      return img;
    }

    // box: the element to sit above (null hides). text/warn: plain strings.
    // long: the reply has run past the range, so the text turns yellow.
    // Red is kept for the incident warning only.
    function show(box, text, warn, long) {
      if (!box || (!text && !warn)) { pill.hidden = true; return; }
      if (!host.isConnected) document.documentElement.appendChild(host);
      pill.textContent = "";
      const img = logo();
      if (img) pill.appendChild(img);
      if (text) {
        const t = document.createElement("span");
        if (long) t.className = "long";
        // The name that opens the line ("Forecast 20s–1m …") is bold, as in the brand preview.
        const name = /^([A-Za-z]+)( .*)$/.exec(text);
        if (name) {
          const b = document.createElement("b");
          b.textContent = name[1];
          t.append(b, name[2]);
        } else {
          t.textContent = text;
        }
        pill.appendChild(t);
      }
      if (warn) {
        const w = document.createElement("span");
        w.className = "warn";
        w.textContent = (text ? " · " : "") + warn;
        pill.appendChild(w);
      }
      const r = box.getBoundingClientRect();
      pill.style.visibility = "hidden";
      pill.style.left = pill.style.top = "0px";   // measure at full available width
      pill.hidden = false;
      const at = place(r, pill.offsetWidth, pill.offsetHeight, window.innerWidth, window.innerHeight);
      if (!at) { pill.hidden = true; return; }
      pill.style.left = at.left + "px";
      pill.style.top = at.top + "px";
      if (coveredByPage({ left: at.left, top: at.top, right: at.left + pill.offsetWidth,
                          bottom: at.top + pill.offsetHeight })) {
        pill.hidden = true;
        return;
      }
      pill.style.visibility = "";
    }
    return { show };
  }

  // Where the pill goes for a box rect, or null when the box is off screen:
  // just above the box, else just below it, always inside the viewport.
  function place(r, w, h, vw, vh) {
    if (!r || r.width <= 0 || r.height <= 0 || r.bottom < 0 || r.top > vh) return null;
    const left = Math.max(MARGIN, Math.min(r.left + 12, vw - w - MARGIN));
    let top = r.top - h - GAP;
    if (top < 4) top = r.bottom + GAP + h <= vh - 4 ? r.bottom + GAP : 4;
    return { left, top: Math.min(top, Math.max(4, vh - h - 4)) };
  }

  function intersects(a, b) {
    return a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
  }

  function isOpen(el) {
    try { if (el.hasAttribute("popover") && !el.matches(":popover-open")) return false; } catch (e) { /* old browser */ }
    const st = getComputedStyle(el);
    return st.display !== "none" && st.visibility !== "hidden" && parseFloat(st.opacity) !== 0;
  }

  function isModal(el) {
    if (el.getAttribute("aria-modal") === "true") return true;
    try { return el.matches("dialog:modal"); } catch (e) { return false; }
  }

  // An open menu, listbox, popover or dialog of the page that overlaps rect,
  // or any open modal.
  function coveredByPage(rect) {
    for (const el of document.querySelectorAll(OVERLAYS)) {
      const r = el.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) continue;
      const near = intersects(rect, { left: r.left - 2, top: r.top - 2, right: r.right + 2, bottom: r.bottom + 2 });
      if ((near || isModal(el)) && isOpen(el)) return true;
    }
    return false;
  }

  root.RainDragonEtaPill = { create, clock, place, intersects, OVERLAYS, Z_INDEX };
})(typeof self !== "undefined" ? self : this);
