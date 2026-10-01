// Regression tests built from REAL request shapes captured from the retailers'
// sites (test/fixtures/real_requests.json). The first version of the extension
// was tested with invented URLs and silently missed Liquorland entirely and
// ignored BWS store selection (a POST). These tests make that impossible.
import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import { readFileSync } from "node:fs";
import { classify } from "../lib/matchers.js";

const real = JSON.parse(readFileSync(new URL("./fixtures/real_requests.json", import.meta.url)));
const inject = readFileSync(new URL("../inject.js", import.meta.url), "utf8");

const concrete = (u) => u.replace("{ids}", "1,2,3").replace("{sku}", "123456").replace("{id}", "abc");
const find = (re, method) => real.filter((r) => re.test(r.url) && (!method || r.method === method));

test("the catalogue contains the endpoints we depend on", () => {
  assert.ok(find(/ProductGroup\/Products\/beer/).length >= 1, "BWS beer list");
  assert.ok(find(/SetPickupByStoreNo/, "POST").length === 1, "BWS store selection (POST)");
  assert.ok(find(/StoreLocator\/Store$/).length === 1, "BWS default store");
  assert.ok(find(/api\/products\/ll\/[a-z]+\/beer-and-cider$/).length >= 1, "Liquorland list");
  assert.ok(find(/danmurphys.*\/Browse$/, "POST").length === 1, "DM Browse");
});

test("every real product-list and store request is recognised", () => {
  for (const r of real) {
    const c = classify(concrete(r.url), r.method, r.method === "POST" ? '{"department":"beer"}' : null);
    const url = r.url;
    if (/ProductGroup\/Products\/beer/.test(url)) assert.deepEqual([c?.role, c?.kind, c?.beer], ["products", "bws_products", true], url);
    else if (/SetPickupByStoreNo|StoreLocator\/Store$|Fulfilment\/Preferences/.test(url)) assert.equal(c?.role, "location", `${r.method} ${url}`);
    else if (/api\/products\/ll\/[a-z]+\/beer-and-cider$/.test(url)) assert.deepEqual([c?.role, c?.kind, c?.beer], ["products", "liquorland_products", true], url);
    else if (/danmurphys.*\/Browse$/.test(url) && r.method === "POST") assert.deepEqual([c?.role, c?.kind, c?.beer], ["products", "dan_murphys_browse", true], url);
  }
});

test("nothing else the retailers' pages request is ever treated as data", () => {
  const wanted = /ProductGroup\/Products\/beer|SetPickupByStoreNo|StoreLocator\/Store$|Fulfilment\/Preferences|api\/products\/ll\/[a-z]+\/beer-and-cider$|danmurphys.*\/apis\/ui\/Browse$/;
  for (const r of real) {
    if (wanted.test(r.url) && !(r.method === "GET" && /Browse$/.test(r.url))) continue;
    const c = classify(concrete(r.url), r.method, '{"department":"beer"}');
    assert.equal(c, null, `${r.method} ${r.url} must be ignored`);
  }
});

test("inject.js and matchers.js agree: every URL classify() accepts is one the injector watches", async () => {
  const posted = [];
  const win = {
    location: { href: "https://www.bws.com.au/", origin: "https://www.bws.com.au" },
    postMessage: (d) => posted.push(d),
    XMLHttpRequest: function () {},
  };
  win.window = win;
  win.fetch = async (url) => ({ url, clone: () => ({ json: async () => ({}) }) });
  vm.runInNewContext(inject, { window: win, location: win.location, URL, decodeURIComponent, JSON, Object, Promise, setTimeout, String });

  for (const r of real) {
    const url = concrete(r.url);
    const accepted = classify(url, r.method, '{"department":"beer"}') !== null;
    posted.length = 0;
    await win.fetch(url, { method: r.method, body: r.method === "POST" ? '{"department":"beer"}' : undefined });
    await new Promise((res) => setTimeout(res, 2));
    if (accepted) assert.equal(posted.length, 1, `injector misses ${r.method} ${url}`);
  }
});
