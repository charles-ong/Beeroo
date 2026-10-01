const $ = (id) => document.getElementById(id);
const DEFAULTS = { consented: false, paused: false };

async function render() {
  const { settings, counters, sentLog } = await chrome.storage.local.get(["settings", "counters", "sentLog"]);
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
