import { sanitizePayload } from "./lib/sanitize.js";
import { SAMPLE_KIND, SAMPLE_RESPONSE } from "./lib/sample.js";
import { newInstallId } from "./lib/contribution.js";

const $ = (id) => document.getElementById(id);
const DEFAULTS = { consented: false, paused: false, serverUrl: "http://127.0.0.1:8000" };

const excerpt = JSON.parse(JSON.stringify(SAMPLE_RESPONSE));
$("before").textContent = JSON.stringify(excerpt, null, 1);
$("after").textContent = JSON.stringify(sanitizePayload(SAMPLE_KIND, SAMPLE_RESPONSE), null, 1);

function isLocal(url) { return /^http:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/.test(url); }

async function load() {
  const { settings, installId } = await chrome.storage.local.get(["settings", "installId"]);
  const s = { ...DEFAULTS, ...(settings || {}) };
  $("consent").checked = s.consented;
  $("ack").checked = s.consented;
  $("server").value = s.serverUrl;
  let id = installId;
  if (!id) { id = newInstallId(); await chrome.storage.local.set({ installId: id }); }
  $("installId").textContent = id;
  await renderLog();
}

async function renderLog() {
  const { sentLog, counters } = await chrome.storage.local.get(["sentLog", "counters"]);
  $("count").textContent = `${(counters && counters.total) || 0} contributions accepted in total`;
  const rows = (sentLog || []).map((e) => {
    const tr = document.createElement("tr");
    const cells = [new Date(e.t).toLocaleString("en-AU"), e.retailer, e.store || "", e.products ?? "", e.ok ? "accepted" : `rejected: ${e.detail || e.status || "error"}`];
    cells.forEach((c, i) => { const td = document.createElement("td"); td.textContent = c; if (i === 4) td.className = e.ok ? "ok" : "bad"; tr.append(td); });
    return tr;
  });
  $("log").replaceChildren(...rows);
}

$("save").addEventListener("click", async () => {
  const wantOn = $("consent").checked;
  if (wantOn && !$("ack").checked) { $("status").textContent = "Please tick the first box too."; $("status").className = "bad"; return; }
  let url = $("server").value.trim().replace(/\/$/, "");
  if (!/^https?:\/\/[^\s/]+(:\d+)?$/.test(url)) { $("status").textContent = "Enter a server like https://example.com"; $("status").className = "bad"; return; }
  if (!isLocal(url)) {
    if (!url.startsWith("https://")) { $("status").textContent = "Non-local servers must use https."; $("status").className = "bad"; return; }
    const ok = await chrome.permissions.request({ origins: [`${url}/*`] });
    if (!ok) { $("status").textContent = "Permission to contact that server was declined."; $("status").className = "bad"; return; }
  }
  const { settings } = await chrome.storage.local.get("settings");
  await chrome.storage.local.set({ settings: { ...DEFAULTS, ...(settings || {}), consented: wantOn, serverUrl: url } });
  $("status").textContent = wantOn ? "Saved. Contributing is ON." : "Saved. Contributing is OFF.";
  $("status").className = "ok";
});

$("reset").addEventListener("click", async () => {
  const id = newInstallId();
  await chrome.storage.local.set({ installId: id, seen: {} });
  $("installId").textContent = id;
});
$("clear").addEventListener("click", async () => { await chrome.storage.local.set({ sentLog: [] }); renderLog(); });

load();
