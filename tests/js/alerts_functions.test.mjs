// Tests for functions/api/alerts/* with an in-memory KV and a fake email service.
import test from "node:test";
import assert from "node:assert/strict";
import { onRequestPost as create } from "../../functions/api/alerts/index.js";
import { onRequestGet as confirm } from "../../functions/api/alerts/confirm.js";
import { onRequestGet as unsub } from "../../functions/api/alerts/unsubscribe.js";
import { onRequestGet as exportAlerts } from "../../functions/api/alerts/export.js";
import { onRequestPost as mark } from "../../functions/api/alerts/mark.js";
import { sha256 } from "../../functions/_lib/alerts.js";

class KV {
  constructor() { this.m = new Map(); }
  async get(k) { return this.m.has(k) ? this.m.get(k) : null; }
  async put(k, v) { this.m.set(k, v); }
  async delete(k) { this.m.delete(k); }
  async list({ prefix }) { return { keys: [...this.m.keys()].filter((k) => k.startsWith(prefix)).map((name) => ({ name })), list_complete: true }; }
}
const mails = [];
globalThis.fetch = async (url, init) => { mails.push({ url, body: JSON.parse(init.body) }); return new Response("{}", { status: 200 }); };
const env = () => ({ ALERTS: new KV(), RESEND_API_KEY: "k", ALERTS_FROM: "Beeroo <a@x.test>", ALERTS_ADMIN_TOKEN: "admin-token-admin-token-1234" });
const post = (body, e, headers = {}) => create({ request: new Request("https://beeroo.test/api/alerts", { method: "POST", body: JSON.stringify(body), headers }), env: e });
const good = { email: "Me@Example.com", product_id: 7, state: "nsw", pack: "case:24", target: "45.5" };
const tokenFrom = () => mails.at(-1).body.text.match(/confirm\?t=([0-9a-f]{48})/)[1];
const get = (fn, path, e, headers = {}) => fn({ request: new Request("https://beeroo.test" + path, { headers }), env: e });

test("creates a pending alert, emails a confirmation, and activates on click", async () => {
  const e = env(); mails.length = 0;
  const r = await post(good, e);
  assert.equal(r.status, 200);
  assert.equal(mails.length, 1);
  assert.equal(mails[0].body.to[0], "me@example.com");
  const res = await get(confirm, "/api/alerts/confirm?t=" + tokenFrom(), e);
  assert.match(await res.text(), /Alert confirmed/);
  const list = await (await get(exportAlerts, "/api/alerts/export", e, { authorization: "Bearer " + e.ALERTS_ADMIN_TOKEN })).json();
  assert.equal(list.alerts.length, 1);
  assert.deepEqual([list.alerts[0].state, list.alerts[0].target, list.alerts[0].pack], ["NSW", 45.5, "case:24"]);
});

test("a confirmed email skips the confirmation for its next alert", async () => {
  const e = env(); mails.length = 0;
  await post(good, e); await get(confirm, "/api/alerts/confirm?t=" + tokenFrom(), e);
  mails.length = 0;
  const r = await post({ ...good, product_id: 8 }, e);
  assert.equal(r.status, 200); assert.equal(mails.length, 0);
});

test("free accounts are capped at 3 alerts; a premium flag lifts it", async () => {
  const e = env();
  for (let i = 1; i <= 3; i++) assert.equal((await post({ ...good, product_id: i }, e)).status, 200);
  assert.equal((await post({ ...good, product_id: 4 }, e)).status, 409);
  await e.ALERTS.put("premium:" + (await sha256("me@example.com")), "1");
  assert.equal((await post({ ...good, product_id: 4 }, e)).status, 200);
});

test("rejects bad input, foreign origins, and swallows honeypot bots", async () => {
  const e = env(); mails.length = 0;
  for (const bad of [{ ...good, email: "nope" }, { ...good, state: "XX" }, { ...good, pack: "x" }, { ...good, target: -1 }, { ...good, product_id: "a" }]) {
    assert.equal((await post(bad, e)).status, 400, JSON.stringify(bad));
  }
  assert.equal((await post(good, e, { origin: "https://evil.test" })).status, 403);
  assert.equal((await post({ ...good, website: "http://spam" }, e)).status, 200);
  assert.equal(mails.length, 0); assert.equal(e.ALERTS.m.size, 0);
});

test("unsubscribe deletes the alert; export and mark need the admin token", async () => {
  const e = env(); mails.length = 0;
  await post(good, e); const t = tokenFrom();
  await get(confirm, "/api/alerts/confirm?t=" + t, e);
  assert.equal((await get(exportAlerts, "/api/alerts/export", e)).status, 403);
  assert.equal((await get(exportAlerts, "/api/alerts/export", e, { authorization: "Bearer wrong" })).status, 403);
  const admin = { authorization: "Bearer " + e.ALERTS_ADMIN_TOKEN };
  const [{ id }] = (await (await get(exportAlerts, "/api/alerts/export", e, admin)).json()).alerts;
  const m = await mark({ request: new Request("https://beeroo.test/api/alerts/mark", { method: "POST", headers: admin, body: JSON.stringify({ updates: [{ id, last_notified_price: 40 }] }) }), env: e });
  assert.equal((await m.json()).updated, 1);
  assert.equal(JSON.parse(await e.ALERTS.get("alert:" + id)).last_notified_price, 40);
  assert.equal((await mark({ request: new Request("https://beeroo.test/x", { method: "POST", body: "{}" }), env: e })).status, 403);
  await get(unsub, "/api/alerts/unsubscribe?t=" + t, e);
  assert.equal((await (await get(exportAlerts, "/api/alerts/export", e, admin)).json()).alerts.length, 0);
  assert.equal(e.ALERTS.m.size, 1);   // only the "confirmed" marker for the email remains
});

test("without a KV binding the endpoint says alerts are off (404)", async () => {
  assert.equal((await post(good, {})).status, 404);
});
