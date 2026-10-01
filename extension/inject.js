// Runs in the PAGE's JavaScript world. It only OBSERVES responses the page
// already received: it never makes requests, never alters what the page sees,
// and any failure here is swallowed so the retailer's site is never affected.
(function () {
  if (window.__beerooInjected) return;
  window.__beerooInjected = true;

  const CANDIDATES = [
    /\/apis\/ui\/Browse$/,
    /\/apis\/ui\/Fulfilment\/Preferences$/,
    /\/apis\/ui\/ProductGroup\/Products\/[^/]+\/?$/,
    /\/apis\/ui\/Address\/SetPickupByStoreNo$/,
    /\/apis\/ui\/StoreLocator\/Store$/,
    /^\/api\/products\/ll\/[a-z]+\/[^/]+\/?$/,
  ];

  function interesting(rawUrl) {
    try {
      const u = new URL(rawUrl, location.href);
      return /(danmurphys|bws|liquorland)\.com\.au$/i.test(u.hostname) &&
        CANDIDATES.some((r) => r.test(decodeURIComponent(u.pathname)));
    } catch (e) { return false; }
  }

  function report(info) {
    try { window.postMessage(Object.assign({ __beeroo: 1 }, info), location.origin); } catch (e) { /* ignore */ }
  }

  const origFetch = window.fetch;
  if (typeof origFetch === "function") {
    window.fetch = function (input, init) {
      const promise = origFetch.apply(this, arguments);
      try {
        const url = typeof input === "string" ? input : (input && input.url) || String(input);
        if (interesting(url)) {
          const method = (init && init.method) || (input && input.method) || "GET";
          const requestBody = init && typeof init.body === "string" ? init.body : null;
          promise.then((res) => {
            try {
              res.clone().json().then((json) => report({ url: res.url || url, method, requestBody, json })).catch(() => {});
            } catch (e) { /* ignore */ }
          }).catch(() => {});
        }
      } catch (e) { /* never break the page */ }
      return promise;
    };
  }

  const XHR = window.XMLHttpRequest && window.XMLHttpRequest.prototype;
  if (XHR) {
    const origOpen = XHR.open, origSend = XHR.send;
    XHR.open = function (method, url) {
      try { this.__beeroo = { method: method, url: String(url) }; } catch (e) { /* ignore */ }
      return origOpen.apply(this, arguments);
    };
    XHR.send = function (body) {
      try {
        const meta = this.__beeroo;
        if (meta && interesting(meta.url)) {
          const requestBody = typeof body === "string" ? body : null;
          this.addEventListener("load", function () {
            try {
              const text = this.responseType === "" || this.responseType === "text" ? this.responseText : null;
              const json = text !== null ? JSON.parse(text) : this.responseType === "json" ? this.response : null;
              if (json) report({ url: this.responseURL || meta.url, method: meta.method, requestBody: requestBody, json: json });
            } catch (e) { /* ignore */ }
          });
        }
      } catch (e) { /* never break the page */ }
      return origSend.apply(this, arguments);
    };
  }
})();
