"use strict";
const RETAILERS = [
  { id: "dan_murphys", name: "Dan Murphy's", color: "var(--dm)" },
  { id: "bws", name: "BWS", color: "var(--bws)" },
  { id: "liquorland", name: "Liquorland", color: "var(--ll)" },
];
const PAGE = 40;
const $ = (id) => document.getElementById(id);
const state = { postcode: "", offset: 0, lastQuery: "" };

// ---- tiny safe DOM builder (text only; never builds HTML strings) ----------
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style") el.style.cssText = v; // CSSOM is allowed by our strict CSP; the style attribute is not
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const money = (n) => "$" + Number(n).toFixed(2);
const esc2 = (n) => Number(n).toFixed(2);
function ago(iso) {
  const days = Math.floor((Date.now() - new Date(iso)) / 864e5);
  if (days < 1) return "today";
  return days === 1 ? "1 day ago" : days + " days ago";
}
function store(key) { try { return localStorage.getItem(key); } catch { return null; } }
function save(key, val) { try { localStorage.setItem(key, val); } catch { /* ignore */ } }

// ---- API --------------------------------------------------------------------
async function api(path, params) {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) if (v !== "" && v != null && v !== false) qs.set(k, v);
  const res = await fetch(path + "?" + qs);
  if (!res.ok) {
    let msg = "Something went wrong. Please try again.";
    try { msg = (await res.json()).detail || msg; } catch { /* keep default */ }
    throw new Error(msg);
  }
  return res.json();
}
function filterParams() {
  return {
    postcode: state.postcode, q: $("q").value.trim(), sort: $("sort").value, pack: $("pack").value,
    min_abv: $("min_abv").value, max_abv: $("max_abv").value,
    include_member: $("include_member").checked, min_retailers: $("multi").checked ? 2 : "",
  };
}

// ---- Rendering --------------------------------------------------------------
function optionFor(entry) {
  // Prefer the best-value option; fall back to cheapest per can/bottle when ABV is unknown.
  return entry.best_value || entry.cheapest_unit;
}
function cell(product, r) {
  const entry = product.retailers[r.id];
  const head = h("div", { class: "rname" }, h("span", { class: "dot", style: "background:" + r.color }), r.name);
  if (!entry) return h("div", { class: "cell empty" }, head, "Not listed");
  const opt = optionFor(entry);
  const isBest = product.best_value_retailer === r.id || (!product.best_value_retailer && product.best_unit_price_retailer === r.id);
  const value = opt.price_per_standard_drink != null
    ? h("div", { class: "val" }, money(opt.price_per_standard_drink) + " / std drink")
    : h("div", { class: "sub" }, "ABV unknown · " + money(opt.unit_price) + " each");
  return h("div", { class: "cell" + (isBest ? " best" : "") }, head,
    h("div", { class: "price" }, money(opt.price)),
    h("div", { class: "sub" }, opt.label + (opt.member_only ? " · member offer" : "")),
    value,
    isBest && h("div", { class: "tagbest" }, opt.price_per_standard_drink != null ? "Best value" : "Lowest per unit"),
    entry.stale && h("div", { class: "stale" }, "Updated " + ago(entry.last_updated)));
}
function abvBadge(p) {
  if (p.abv == null) return h("span", { class: "badge warn" }, "ABV unknown");
  const own = p.retailers[p.abv_source];
  const note = p.abv_source && !own ? " · ABV from " + (RETAILERS.find((r) => r.id === p.abv_source)?.name || p.abv_source) : "";
  return h("span", { class: "badge" }, p.abv + "% ABV" + note);
}
function card(p) {
  const node = h("article", { class: "card", tabindex: "0", role: "button", "aria-label": "Details for " + p.name },
    h("div", {}, h("h2", {}, p.name),
      h("div", { class: "meta" }, p.unit_volume_ml ? h("span", {}, Math.round(p.unit_volume_ml) + " mL") : null, abvBadge(p))),
    RETAILERS.map((r) => cell(p, r)));
  const open = () => openDetail(p.id);
  node.addEventListener("click", open);
  node.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
  return node;
}
function renderLocations(loc) {
  $("notices").replaceChildren(...loc.notices.map((n) => h("div", { class: "banner notice" }, n)));
  const parts = RETAILERS.map((r) => {
    const l = loc.retailers[r.id];
    if (!l) return null;
    const name = l.store_name ? l.store_name + (l.suburb && l.suburb !== l.store_name ? ", " + l.suburb : "") : l.state + " pricing";
    const where = name.includes(l.state) ? name : `${name} (${l.state})`;
    return `${r.name}: ${where}`;
  }).filter(Boolean);
  $("stores").textContent = parts.length ? "Prices from — " + parts.join(" · ") : "";
}

async function search(append) {
  if (!state.postcode) return;
  if (!append) state.offset = 0;
  try {
    const data = await api("/api/compare", { ...filterParams(), limit: PAGE, offset: state.offset });
    $("demo-banner").hidden = !data.meta.demo;
    renderLocations(data.locations);
    $("filters").hidden = false;
    const list = $("results");
    if (!append) list.replaceChildren();
    if (!data.products.length && !append) {
      list.append(h("div", { class: "empty-state" }, "No products match these filters for this postcode. Try clearing some filters."));
    }
    data.products.forEach((p) => list.append(card(p)));
    state.offset += data.products.length;
    $("more").hidden = state.offset >= data.meta.total;
    $("summary").textContent = `${data.meta.total} product${data.meta.total === 1 ? "" : "s"} · postcode ${state.postcode} (${data.locations.state})`
      + (data.meta.latest_data ? " · latest data " + ago(data.meta.latest_data) : "");
    const url = new URL(location.href);
    url.search = new URLSearchParams({ postcode: state.postcode }).toString();
    history.replaceState(null, "", url);
  } catch (e) {
    showPostcodeError(e.message);
  }
}

// ---- Detail + chart ---------------------------------------------------------
function chart(history, seriesKey) {
  const W = 640, H = 220, L = 44, R = 12, T = 10, B = 28;
  const lines = RETAILERS.map((r) => ({ r, s: history[r.id]?.[seriesKey] })).filter((x) => x.s);
  const pts = lines.flatMap((x) => x.s.points.map((p) => ({ t: +new Date(p.t), v: p.price })));
  if (!pts.length) return h("p", { class: "sub" }, "No history for this pack.");
  const t0 = Math.min(...pts.map((p) => p.t)), t1 = Math.max(...pts.map((p) => p.t));
  const lo = Math.min(...pts.map((p) => p.v)), hi = Math.max(...pts.map((p) => p.v));
  const pad = (hi - lo) * 0.15 || 1, y0 = Math.max(0, lo - pad), y1 = hi + pad;
  const X = (t) => L + (t1 === t0 ? (W - L - R) / 2 : ((t - t0) / (t1 - t0)) * (W - L - R));
  const Y = (v) => T + (1 - (v - y0) / (y1 - y0)) * (H - T - B);
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Price history chart");
  const add = (tag, attrs, text) => {
    const e = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    if (text) e.textContent = text;
    svg.append(e); return e;
  };
  for (let i = 0; i <= 4; i++) {
    const v = y0 + ((y1 - y0) * i) / 4;
    add("line", { x1: L, x2: W - R, y1: Y(v), y2: Y(v), class: "grid" });
    add("text", { x: L - 6, y: Y(v) + 4, "text-anchor": "end" }, "$" + v.toFixed(0));
  }
  const fmt = (t) => new Date(t).toLocaleDateString("en-AU", { day: "numeric", month: "short" });
  add("text", { x: L, y: H - 8 }, fmt(t0));
  if (t1 !== t0) add("text", { x: W - R, y: H - 8, "text-anchor": "end" }, fmt(t1));
  lines.forEach(({ r, s }) => {
    const color = getComputedStyle(document.documentElement).getPropertyValue(r.color.slice(4, -1)) || "#888";
    const d = s.points.map((p, i) => `${i ? "L" : "M"}${X(+new Date(p.t)).toFixed(1)},${Y(p.price).toFixed(1)}`).join(" ");
    if (s.points.length > 1) add("path", { d, fill: "none", stroke: color, "stroke-width": 2 });
    s.points.forEach((p) => add("circle", { cx: X(+new Date(p.t)), cy: Y(p.price), r: 3.5, fill: color }));
  });
  return svg;
}
const SIGNALS = {
  lowest_seen: ["Lowest price we've recorded", "good"], below_usual: ["Below its usual price", "good"],
  usual: ["Around its usual price", ""], above_usual: ["Above its usual price", "bad"],
  not_enough_history: ["Not enough history yet to say if this is a good price", ""],
};
function detailBody(d) {
  const p = d.product;
  const keys = [...new Set(Object.values(d.history).flatMap((h2) => Object.keys(h2)))]
    .sort((a, b) => a.split(":")[1] - b.split(":")[1] || a.localeCompare(b));
  const defaultKey = keys.find((k) => k.endsWith(":0") && k.startsWith("case")) || keys.find((k) => k.endsWith(":0")) || keys[0];
  const chartBox = h("div", { class: "chart-wrap" });
  const info = h("div", {});
  const select = h("select", { "aria-label": "Pack to chart", onchange: () => draw(select.value) },
    keys.map((k) => {
      const lab = Object.values(d.history).map((x) => x[k]?.label).find(Boolean);
      return h("option", { value: k, selected: k === defaultKey }, lab);
    }));
  function draw(k) {
    chartBox.replaceChildren(chart(d.history, k));
    const rows = RETAILERS.map((r) => ({ r, s: d.history[r.id]?.[k] })).filter((x) => x.s);
    info.replaceChildren(...[
      h("div", { class: "legend" }, rows.map(({ r }) => h("span", {}, h("span", { class: "dot", style: "background:" + r.color }), " " + r.name))),
      ...rows.map(({ r, s }) => {
        const [text, cls] = SIGNALS[s.signal];
        return h("p", { class: "signal " + cls }, `${r.name}: ${text}`,
          h("span", { class: "sub" }, `  (now ${money(s.current)} · low ${money(s.min)} · high ${money(s.max)} · ${s.n} price${s.n === 1 ? "" : "s"} since ${new Date(s.first_seen).toLocaleDateString("en-AU")})`));
      }),
      rows.every(({ s }) => s.n < 2) ? h("p", { class: "sub" }, "History builds up as prices are re-collected over time.") : null,
    ].filter(Boolean));
  }
  const table = h("div", { class: "tbl" }, h("table", {},
    h("thead", {}, h("tr", {}, ["Retailer", "Pack", "Price", "$ / std drink", "$ each", "Updated"].map((c) => h("th", {}, c)))),
    h("tbody", {}, RETAILERS.flatMap((r) => (p.retailers[r.id]?.options || []).map((o, i) => h("tr", {},
      h("td", {}, i === 0 ? h("a", { href: p.retailers[r.id].url, target: "_blank", rel: "noopener noreferrer" }, r.name) : ""),
      h("td", {}, o.label + (o.member_only ? " (member offer)" : "")),
      h("td", { class: "num" }, money(o.price)),
      h("td", { class: "num" }, o.price_per_standard_drink != null ? money(o.price_per_standard_drink) : "ABV unknown"),
      h("td", { class: "num" }, money(o.unit_price)),
      h("td", {}, ago(o.observed_at))))))));
  const root = h("div", {}, h("h2", { id: "detail-title" }, p.name),
    h("div", { class: "meta" }, p.unit_volume_ml ? h("span", {}, Math.round(p.unit_volume_ml) + " mL") : null, abvBadge(p)),
    h("h3", {}, "Prices"), table,
    h("h3", {}, "Price history"),
    d.demo ? h("p", { class: "banner demo" }, "Demo data: this history is simulated.") : null,
    keys.length ? [select, chartBox, info] : h("p", { class: "sub" }, "No history yet."));
  if (keys.length) draw(defaultKey);
  return root;
}
async function openDetail(id) {
  try {
    const d = await api("/api/products/" + id, { postcode: state.postcode });
    $("detail-body").replaceChildren(detailBody(d));
    $("detail").showModal();
  } catch (e) { alert(e.message); }
}

// ---- Wiring -----------------------------------------------------------------
function showPostcodeError(msg) {
  const el = $("postcode-error");
  el.textContent = msg; el.hidden = !msg;
}
function submitPostcode(e) {
  e && e.preventDefault();
  const pc = $("postcode").value.trim();
  if (!/^\d{4}$/.test(pc)) return showPostcodeError("Enter a valid 4-digit Australian postcode.");
  showPostcodeError("");
  state.postcode = pc; save("beeroo.postcode", pc);
  search(false);
}
$("postcode-form").addEventListener("submit", submitPostcode);
$("more").addEventListener("click", () => search(true));
$("detail-close").addEventListener("click", () => $("detail").close());
$("detail").addEventListener("click", (e) => { if (e.target === $("detail")) $("detail").close(); });
let timer;
for (const id of ["q", "min_abv", "max_abv"]) $(id).addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => search(false), 300); });
for (const id of ["sort", "pack", "include_member", "multi"]) $(id).addEventListener("change", () => search(false));

const fromUrl = new URLSearchParams(location.search).get("postcode");
const initial = fromUrl || store("beeroo.postcode");
if (initial && /^\d{4}$/.test(initial)) { $("postcode").value = initial; submitPostcode(); }
