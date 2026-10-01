import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import { readFileSync } from "node:fs";

const code = readFileSync(new URL("../inject.js", import.meta.url), "utf8");

function makeWindow({ fetchImpl } = {}) {
  const posted = [];
  class FakeXHR {
    open(method, url) { this.openArgs = [method, url]; }
    send() { setTimeout(() => this.listeners && this.listeners.load && this.listeners.load.call(this), 0); }
    addEventListener(ev, fn) { (this.listeners ||= {})[ev] = fn; }
  }
  const win = {
    location: { href: "https://www.bws.com.au/beer", origin: "https://www.bws.com.au" },
    postMessage: (data, origin) => posted.push({ data, origin }),
    fetch: fetchImpl, XMLHttpRequest: FakeXHR,
  };
  win.window = win;
  vm.runInNewContext(code, { window: win, location: win.location, URL, decodeURIComponent, JSON, Object, Promise, setTimeout, String });
  return { win, posted, FakeXHR };
}

const response = (json, url) => ({ url, clone() { return { json: async () => json }; } });
const tick = () => new Promise((r) => setTimeout(r, 5));

test("fetch: matching responses are reported, the page still gets the original response", async () => {
  const url = "https://api.bws.com.au/apis/ui/ProductGroup/Products/beer_bestsellers";
  const original = response({ Items: [] }, url);
  const { win, posted } = makeWindow({ fetchImpl: async () => original });
  const got = await win.fetch(url, { method: "GET" });
  assert.equal(got, original);
  await tick();
  assert.equal(posted.length, 1);
  assert.equal(posted[0].origin, "https://www.bws.com.au");
  assert.deepEqual([posted[0].data.__beeroo, posted[0].data.url, posted[0].data.method], [1, url, "GET"]);
});

test("fetch: POST body is passed along for category detection", async () => {
  const url = "https://api.danmurphys.com.au/apis/ui/Browse";
  const { win, posted } = makeWindow({ fetchImpl: async () => response({ Bundles: [] }, url) });
  await win.fetch(url, { method: "POST", body: '{"department":"beer"}' });
  await tick();
  assert.equal(posted[0].data.requestBody, '{"department":"beer"}');
});

test("fetch: unrelated URLs are never inspected or reported", async () => {
  let cloned = false;
  const res = { url: "x", clone() { cloned = true; return { json: async () => ({}) }; } };
  const { win, posted } = makeWindow({ fetchImpl: async () => res });
  await win.fetch("https://api.bws.com.au/apis/ui/Trolley");
  await win.fetch("https://evil.example.com/apis/ui/Browse");
  await tick();
  assert.equal(cloned, false);
  assert.equal(posted.length, 0);
});

test("fetch: failures in reading or reporting never break or change the page's call", async () => {
  const url = "https://api.bws.com.au/apis/ui/ProductGroup/Products/beer_all";
  const bad = { url, clone() { throw new Error("boom"); } };
  const { win } = makeWindow({ fetchImpl: async () => bad });
  assert.equal(await win.fetch(url), bad);
  const rejecting = makeWindow({ fetchImpl: () => Promise.reject(new Error("net")) });
  await assert.rejects(rejecting.win.fetch(url), /net/);   // the page still sees ITS error
  const notJson = makeWindow({ fetchImpl: async () => ({ url, clone: () => ({ json: async () => { throw new Error("not json"); } }) }) });
  await notJson.win.fetch(url);
  await tick();
  assert.equal(notJson.posted.length, 0);
});

test("XHR: matching responses are reported; others are not", async () => {
  const { win, posted, FakeXHR } = makeWindow({ fetchImpl: async () => ({}) });
  const x = new win.XMLHttpRequest();
  x.open("GET", "https://api.bws.com.au/apis/ui/Address/SetPickupByStoreNo");
  x.responseType = ""; x.responseText = '{"Success":true}'; x.responseURL = "https://api.bws.com.au/apis/ui/Address/SetPickupByStoreNo";
  x.send();
  const y = new win.XMLHttpRequest();
  y.open("GET", "https://api.bws.com.au/apis/ui/Trolley"); y.responseType = ""; y.responseText = "{}"; y.send();
  await tick();
  assert.equal(posted.length, 1);
  assert.deepEqual(posted[0].data.json, { Success: true });
  assert.ok(x instanceof FakeXHR);
});

test("injecting twice does not double-wrap", async () => {
  const url = "https://api.bws.com.au/apis/ui/ProductGroup/Products/beer_all";
  const { win, posted } = makeWindow({ fetchImpl: async () => response({ Items: [] }, url) });
  vm.runInNewContext(code, { window: win, location: win.location, URL, decodeURIComponent, JSON, Object, Promise, setTimeout, String });
  await win.fetch(url);
  await tick();
  assert.equal(posted.length, 1);
});
