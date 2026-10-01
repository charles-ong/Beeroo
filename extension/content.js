// Isolated-world relay: page (inject.js) -> background service worker.
// Also gathers a few short header/nav texts so the background can check the
// visitor is logged out. It reads nothing else from the page.
(function () {
  function collectTexts() {
    const out = [];
    const nodes = document.querySelectorAll(
      "header a, header button, nav a, nav button, [class*='login' i], [class*='signin' i], [class*='account' i], [class*='sign-in' i]"
    );
    for (const el of nodes) {
      const t = (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim();
      if (t && t.length <= 40) out.push(t);
      if (out.length >= 80) break;
    }
    return out;
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
