import { createPipeline } from "./lib/pipeline.js";
import { newInstallId } from "./lib/contribution.js";

const VERSION = chrome.runtime.getManifest().version;
const DEFAULTS = { consented: false, paused: false, serverUrl: "http://127.0.0.1:8000" };
const MAX_LOG = 100;

async function getSettings() {
  const { settings } = await chrome.storage.local.get("settings");
  return { ...DEFAULTS, ...(settings || {}) };
}

async function getInstallId() {
  const { installId } = await chrome.storage.local.get("installId");
  if (installId) return installId;
  const id = newInstallId();
  await chrome.storage.local.set({ installId: id });
  return id;
}

async function updateBadge() {
  const { counters } = await chrome.storage.local.get("counters");
  const today = new Date().toISOString().slice(0, 10);
  const n = counters && counters.day === today ? counters.sent : 0;
  chrome.action.setBadgeText({ text: n ? String(n) : "" });
  chrome.action.setBadgeBackgroundColor({ color: "#a15c00" });
}

const session = chrome.storage.session;
const pipeline = createPipeline({
  version: VERSION,
  now: () => Date.now(),
  getSettings,
  getInstallId,
  async getLocation(tabId, retailer) {
    const { locs } = await session.get("locs");
    return (locs || {})[`${tabId}:${retailer}`] || null;
  },
  async setLocation(tabId, retailer, loc) {
    const { locs } = await session.get("locs");
    await session.set({ locs: { ...(locs || {}), [`${tabId}:${retailer}`]: loc } });
  },
  async getPending(tabId) {
    const { pending } = await session.get("pending");
    return (pending || {})[tabId] || [];
  },
  async setPending(tabId, list) {
    const { pending } = await session.get("pending");
    await session.set({ pending: { ...(pending || {}), [tabId]: list } });
  },
  async getSeen() { return (await chrome.storage.local.get("seen")).seen || {}; },
  async setSeen(seen) { await chrome.storage.local.set({ seen }); },
  async getSendTimes() { return (await chrome.storage.local.get("sendTimes")).sendTimes || []; },
  async setSendTimes(sendTimes) { await chrome.storage.local.set({ sendTimes }); },
  async send(body) {
    const { serverUrl } = await getSettings();
    const res = await fetch(`${serverUrl.replace(/\/$/, "")}/api/contrib`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
      credentials: "omit",
    });
    let parsed = null;
    try { parsed = await res.json(); } catch { /* non-JSON error body */ }
    return { status: res.status, body: parsed };
  },
  async diag(event) {
    const { diag } = await chrome.storage.local.get("diag");
    const d = diag || { byRetailer: {}, samples: {} };
    const r = (d.byRetailer[event.retailer] ||= {});
    const slot = (r[event.reason] ||= { count: 0, last: 0 });
    slot.count += 1;
    slot.last = event.t;
    if (event.texts) d.samples[event.retailer] = event.texts;
    if (event.store) d.lastStore = { ...(d.lastStore || {}), [event.retailer]: event.store };
    await chrome.storage.local.set({ diag: d });
  },
  async log(entry) {
    const { sentLog } = await chrome.storage.local.get("sentLog");
    const next = [entry, ...(sentLog || [])].slice(0, MAX_LOG);
    const { counters } = await chrome.storage.local.get("counters");
    const today = new Date().toISOString().slice(0, 10);
    const c = counters && counters.day === today ? counters : { day: today, sent: 0, total: (counters && counters.total) || 0 };
    if (entry.ok) { c.sent += 1; c.total += 1; }
    await chrome.storage.local.set({ sentLog: next, counters: c });
    updateBadge();
  },
});

chrome.runtime.onMessage.addListener((message, sender) => {
  if (!message || message.type !== "response" || !sender.tab) return;
  pipeline.handle({ tabId: sender.tab.id, ...message }).catch(() => {});
});

chrome.tabs.onRemoved.addListener(async (tabId) => {
  const { locs, pending } = await session.get(["locs", "pending"]);
  const prefix = `${tabId}:`;
  const cleaned = Object.fromEntries(Object.entries(locs || {}).filter(([k]) => !k.startsWith(prefix)));
  const p = { ...(pending || {}) }; delete p[tabId];
  await session.set({ locs: cleaned, pending: p });
});

chrome.runtime.onInstalled.addListener(({ reason }) => {
  if (reason === "install") chrome.runtime.openOptionsPage();
  updateBadge();
});
chrome.runtime.onStartup.addListener(updateBadge);
