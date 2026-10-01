import { explain, RETAILER_NAMES } from "./lib/reasons.js";
const $ = (id) => document.getElementById(id);
const DEFAULTS = { consented: false, paused: false };

async function render() {
  const { settings, counters, sentLog, diag } = await chrome.storage.local.get(["settings", "counters", "sentLog", "diag"]);
  const s = { ...DEFAULTS, ...(settings || {}) };
  const today = new Date().toISOString().slice(0, 10);
  const n = counters && counters.day === today ? counters.sent : 0;

  if (!s.consented) {
    $("state").textContent = "Off. Open settings to review and turn it on.";
    $("toggle").hidden = true;
  } else {
    $("state").textContent = s.paused ? "Paused." : "On: sharing logged-out beer pages only.";
    $("state").className = s.paused ? "muted" : "ok";
    $("toggle").hidden = false;
    $("toggle").textContent = s.paused ? "Resume" : "Pause";
  }
  $("today").textContent = `${n} accepted today`;
  const lines = (sentLog || []).slice(0, 5).map((e) => {
    const d = document.createElement("div");
    d.textContent = `${new Date(e.t).toLocaleTimeString("en-AU")} ${e.retailer} ${e.ok ? "accepted" : "rejected"}`;
    return d;
  });
  const why = Object.keys(RETAILER_NAMES).map((r) => {
    const reasons = (diag && diag.byRetailer && diag.byRetailer[r]) || {};
    const latest = Object.entries(reasons).filter(([k]) => k !== "store_seen").sort((a, b) => b[1].last - a[1].last)[0];
    const d = document.createElement("div");
    if (!latest) { d.textContent = `${RETAILER_NAMES[r]}: nothing recognised yet`; return d; }
    const [cls, text] = explain(latest[0]);
    d.textContent = `${RETAILER_NAMES[r]}: ${text}`;
    d.className = cls;
    return d;
  });
  $("why").replaceChildren(...why);
  $("recent").replaceChildren(...(lines.length ? lines : ["Nothing sent yet."]));
}

$("toggle").addEventListener("click", async () => {
  const { settings } = await chrome.storage.local.get("settings");
  const s = { ...DEFAULTS, ...(settings || {}) };
  await chrome.storage.local.set({ settings: { ...s, paused: !s.paused } });
  render();
});
$("options").addEventListener("click", () => chrome.runtime.openOptionsPage());
render();
