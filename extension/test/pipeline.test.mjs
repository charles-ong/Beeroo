import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createPipeline, MAX_PER_HOUR, PENDING_MS } from "../lib/pipeline.js";

const fx = (n) => JSON.parse(readFileSync(new URL(`../../tests/fixtures/${n}`, import.meta.url)));
const LOGGED_OUT = ["Offers", "Login"];
const LL = "https://www.liquorland.com.au/api/products/ll_act/beer-and-cider";
const BWS_LIST = "https://api.bws.com.au/apis/ui/ProductGroup/Products/beer_bestsellers";
const BWS_PICKUP = "https://api.bws.com.au/apis/ui/Address/SetPickupByStoreNo";
const DM_BROWSE = "https://api.danmurphys.com.au/apis/ui/Browse";
const DM_PREFS = "https://api.danmurphys.com.au/apis/ui/Fulfilment/Preferences";
const DM_PREFS_JSON = { FulfilmentMethod: "Click & Collect", ClickAndCollectDetails: { FulfilmentStoreID: "1546", FulfilmentStoreName: "Thornleigh", AddressSuburb: "THORNLEIGH", AddressState: "NSW", AddressPostalCode: "2120" } };

function harness(over = {}) {
  const mem = { locs: {}, pending: {}, seen: {}, times: [], logs: [], sent: [], clock: 1_000_000 };
  const deps = {
    version: "0.1.0",
    now: () => mem.clock,
    getSettings: async () => ({ consented: true, paused: false, serverUrl: "x", ...(over.settings || {}) }),
    getInstallId: async () => "00000000-0000-4000-8000-000000000001",
    getLocation: async (t, r) => mem.locs[`${t}:${r}`] || null,
    setLocation: async (t, r, l) => { mem.locs[`${t}:${r}`] = l; },
    getPending: async (t) => mem.pending[t] || [],
    setPending: async (t, l) => { mem.pending[t] = l; },
    getSeen: async () => mem.seen, setSeen: async (s) => { mem.seen = s; },
    getSendTimes: async () => mem.times, setSendTimes: async (t) => { mem.times = t; },
    send: over.send || (async (body) => { mem.sent.push(body); return { status: 202, body: { status: "accepted", products: 3 } }; }),
    log: async (e) => { mem.logs.push(e); },
  };
  const p = createPipeline(deps);
  const call = (m) => p.handle({ tabId: 1, texts: LOGGED_OUT, method: "GET", ...m });
  return { mem, call };
}

const dmList = (over = {}) => ({ url: DM_BROWSE, method: "POST", requestBody: '{"department":"beer"}', json: fx("dan_murphys_browse_page1.json"), ...over });

test("nothing is sent without consent, or while paused", async () => {
  for (const settings of [{ consented: false }, { paused: true }]) {
    const h = harness({ settings });
    const r = await h.call({ url: LL, json: fx("liquorland_act_products.json") });
    assert.equal(r.action, "skip");
    assert.equal(h.mem.sent.length, 0);
  }
});

test("Liquorland list from a logged-out page is sent once, then de-duplicated", async () => {
  const h = harness();
  const msg = { url: LL, json: fx("liquorland_act_products.json") };
  assert.equal((await h.call(msg)).action, "sent");
  assert.equal(h.mem.sent.length, 1);
  const body = h.mem.sent[0];
  assert.equal(body.kind, "liquorland_products");
  assert.equal(body.logged_in, false);
  assert.equal(body.location, null);
  assert.equal(body.payload.debugQuery, "sitestate=ll_act");
  assert.deepEqual(await h.call(msg), { action: "skip", reason: "already_sent" });
  assert.equal(h.mem.sent.length, 1);
});

test("possible or definite logged-in sessions send nothing", async () => {
  for (const texts of [["Hi Sam", "My account"], ["Log out"], []]) {
    const h = harness();
    const r = await h.call({ url: LL, json: fx("liquorland_act_products.json"), texts });
    assert.equal(r.reason, "maybe_logged_in");
    assert.equal(h.mem.sent.length, 0);
  }
});

test("non-beer pages send nothing", async () => {
  const h = harness();
  const r = await h.call({ url: "https://www.liquorland.com.au/api/products/ll_act/wine", json: fx("liquorland_act_products.json") });
  assert.equal(r.reason, "not_beer");
  assert.equal((await h.call(dmList({ requestBody: '{"department":"spirits"}' }))).reason, "not_beer");
});

test("BWS: store first, then products", async () => {
  const h = harness();
  assert.equal((await h.call({ url: BWS_PICKUP, json: fx("bws_2606_set_pickup.json") })).action, "location_set");
  assert.equal((await h.call({ url: BWS_LIST, json: fx("bws_2606_products.json") })).action, "sent");
  assert.deepEqual(h.mem.sent[0].location, { store_id: "6723", store_name: "Woden", suburb: "Woden", state: "ACT", postcode: "2606" });
});

test("Dan Murphy's: products arrive before the store, then flush when it arrives", async () => {
  const h = harness();
  assert.equal((await h.call(dmList())).action, "waiting_for_store");
  assert.equal(h.mem.sent.length, 0);
  h.mem.clock += 5000;
  const r = await h.call({ url: DM_PREFS, json: DM_PREFS_JSON });
  assert.equal(r.flushed, 1);
  assert.equal(h.mem.sent.length, 1);
  assert.equal(h.mem.sent[0].location.store_id, "1546");
});

test("waiting products expire if the store never arrives in time", async () => {
  const h = harness();
  await h.call(dmList());
  h.mem.clock += PENDING_MS + 1000;
  const r = await h.call({ url: DM_PREFS, json: DM_PREFS_JSON });
  assert.equal(r.flushed, 0);
  assert.equal(h.mem.sent.length, 0);
});

test("delivery mode never yields a location, so nothing is sent", async () => {
  const h = harness();
  await h.call(dmList());
  const r = await h.call({ url: DM_PREFS, json: { ...DM_PREFS_JSON, FulfilmentMethod: "Delivery" } });
  assert.equal(r.reason, "location_unusable");
  assert.equal(h.mem.sent.length, 0);
});

test("a store from one tab is not used for another tab", async () => {
  const h = harness();
  await h.call({ tabId: 1, url: BWS_PICKUP, json: fx("bws_2606_set_pickup.json") });
  assert.equal((await h.call({ tabId: 2, url: BWS_LIST, json: fx("bws_2606_products.json") })).action, "waiting_for_store");
});

test("local rate limit stays under the server's", async () => {
  const h = harness();
  const base = fx("liquorland_act_products.json");
  for (let i = 0; i < MAX_PER_HOUR; i++) {
    const json = { ...base, products: base.products.slice(0, 3).map((p) => ({ ...p, name: `${p.name} v${i}` })) };
    assert.equal((await h.call({ url: LL, json })).action, "sent");
  }
  const json = { ...base, products: base.products.slice(0, 2) };
  assert.equal((await h.call({ url: LL, json })).reason, "rate_limited");
  h.mem.clock += 3600e3 + 1;
  assert.equal((await h.call({ url: LL, json })).action, "sent");
});

test("a server rejection is logged, not de-duplicated, and not hidden", async () => {
  const h = harness({ send: async () => ({ status: 422, body: { detail: "parse: no usable products" } }) });
  const msg = { url: LL, json: fx("liquorland_act_products.json") };
  assert.equal((await h.call(msg)).action, "rejected");
  assert.equal(h.mem.logs.at(-1).ok, false);
  assert.match(h.mem.logs.at(-1).detail, /parse/);
  assert.equal((await h.call(msg)).action, "rejected"); // can retry later
});

test("network failure is logged and does not count against the rate limit", async () => {
  const h = harness({ send: async () => { throw new Error("offline"); } });
  const r = await h.call({ url: LL, json: fx("liquorland_act_products.json") });
  assert.equal(r.action, "error");
  assert.equal(h.mem.times.length, 0);
});

test("unrelated and malformed responses are ignored", async () => {
  const h = harness();
  assert.equal((await h.call({ url: "https://example.com/x", json: {} })).reason, "ignored");
  assert.equal((await h.call({ url: LL, json: { hello: 1 } })).reason, "unrecognised_shape");
  assert.equal(h.mem.sent.length, 0);
});

test("what is sent contains no personalised fields", async () => {
  const h = harness();
  const raw = fx("bws_2606_products.json");
  raw.Items[0].Products[0].QuantityInTrolley = 3;
  raw.Items[0].Products[0].IsWatched = true;
  await h.call({ url: BWS_PICKUP, json: fx("bws_2606_set_pickup.json") });
  await h.call({ url: BWS_LIST, json: raw });
  const text = JSON.stringify(h.mem.sent[0]);
  for (const f of ["QuantityInTrolley", "IsWatched", "RichDescription", "Westfields"]) assert.ok(!text.includes(f), f);
});
