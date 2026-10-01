// Isolated-world relay: page (inject.js) -> background service worker.
// Also gathers a few short header/nav texts so the background can check the
// visitor is logged out. It reads nothing else from the page.
(function () {
  // Sites use custom elements and generated class names (e.g. Dan Murphy's
  // "Login" is a <span> inside <li class="nav-right__item"> in <shop-desktop-header>),
  // so select by POSITION: short visible text near the top of the page.
  const HEADER_PX = 220;
  let cache = { t: 0, texts: [] };

  function collectTexts() {
    const now = Date.now();
    if (now - cache.t < 3000) return cache.texts;
    const out = new Set();
    let scanned = 0;
    for (const el of document.querySelectorAll("a, button, span, div, li, p")) {
      if (++scanned > 4000 || out.size >= 80) break;
      if (el.children.length > 3) continue; // skip big containers
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0 || r.top + window.scrollY > HEADER_PX) continue;
      const t = (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim();
      if (t && t.length <= 40) out.add(t);
    }
    cache = { t: now, texts: [...out] };
    return cache.texts;
  }

  window.addEventListener("message", (event) => {
    if (event.source !== window || event.origin !== location.origin) return;
    const d = event.data;
    if (!d || d.__beeroo !== 1 || typeof d.url !== "string") return;
    try {
      chrome.runtime.sendMessage({
        type: "response", url: d.url, method: d.method, requestBody: d.requestBody,
        json: d.json, texts: collectTexts(),
      }).catch(() => {});
    } catch (e) { /* extension reloaded; ignore */ }
  });
})();
