"use strict";
const RETAILERS = [
  { id: "dan_murphys", name: "Dan Murphy's", color: "var(--dm)" },
  { id: "bws", name: "BWS", color: "var(--bws)" },
  { id: "liquorland", name: "Liquorland", color: "var(--ll)" },
];
const PAGE = 40;
const $ = (id) => document.getElementById(id);
const state_ = { region: "", offset: 0, states: [], retailers: new Set() };
const ABV_STEPS = [3, 3.5, 4, 4.5, 5, 5.5, 6, 7, 8, 10];

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
// Five stars with the right share filled (width set through CSSOM: our CSP forbids style attributes).
function stars(rating) {
  const fill = h("span", { class: "fill", "aria-hidden": "true" }, "★★★★★");
  fill.style.width = Math.max(0, Math.min(5, rating)) / 5 * 100 + "%";
  return h("span", { class: "stars", role: "img", "aria-label": rating.toFixed(1) + " out of 5 stars" },
    h("span", { "aria-hidden": "true" }, "★★★★★"), fill);
}
function ratingBadge(p) {
  if (!p.rating) return null;
  return h("span", { class: "rating", title: "Average of " + p.review_count + " review" + (p.review_count === 1 ? "" : "s") + " across retailers" },
    stars(p.rating), p.rating.toFixed(1), p.review_count ? h("span", { class: "count" }, "(" + p.review_count + ")") : null);
}
const esc2 = (n) => Number(n).toFixed(2);
function ago(iso) {
  const days = Math.floor((Date.now() - new Date(iso)) / 864e5);
  if (days < 1) return "today";
  return days === 1 ? "1 day ago" : days + " days ago";
}
function store(key) { try { return localStorage.getItem(key); } catch { return null; } }
function save(key, val) { try { localStorage.setItem(key, val); } catch { /* ignore */ } }

// ---- Data source: live API (server) or static files (free hosting) -----------
const STATIC_MODE = (document.querySelector('meta[name="beeroo-mode"]') || {}).content === "static";
const jsonCache = new Map();
async function loadJson(path) {
  if (!jsonCache.has(path)) {
    jsonCache.set(path, fetch(path).then((r) => {
      if (!r.ok) throw new Error(r.status === 404 ? "That data isn't available." : "Couldn't load data. Please try again.");
      return r.json();
    }));
  }
  try { return await jsonCache.get(path); } catch (e) { jsonCache.delete(path); throw e; }
}
async function staticState(region) {
  if (!region) throw new Error("Choose your state or territory.");
  const manifest = await loadJson("data/manifest.json");
  if (!manifest.states.includes(region)) {
    throw new Error(`No prices for ${region} yet. Available now: ${manifest.states.join(", ") || "none"}.`);
  }
  return region;
}
async function staticCompare(p, limit, offset) {
  const S = window.BeerooStatic;
  const state = await staticState(p.state);
  const payload = await loadJson(S.variantPath(state));
  return S.applyQuery(payload, { ...p, limit, offset });
}
async function staticDetail(id) {
  const state = await staticState(state_.region);
  return loadJson(window.BeerooStatic.detailPath(state, id));
}

// ---- API --------------------------------------------------------------------
async function api(path, params) {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v == null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => qs.append(k, x));
    else qs.set(k, v);          // booleans go as "true"/"false": the server's default for member offers is "on"
  }
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
    state: state_.region, q: $("q").value.trim(), sort: $("sort").value, reverse: $("reverse").getAttribute("aria-pressed") === "true",
    types: selectedTypes(), retailers: [...state_.retailers],
    min_units: $("min_units").value, max_units: $("max_units").value,
    min_abv: $("min_abv").value, max_abv: $("max_abv").value,
    include_member: $("include_member").checked, min_retailers: $("multi").checked ? 2 : "",
    best_only: $("best_only").checked && state_.retailers.size > 0,
  };
}
// The API names differ slightly from the form's (repeatable "retailer" and "type").
function apiParams(p) {
  const { types, retailers, ...rest } = p;
  return { ...rest, type: types, retailer: retailers };
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
      h("div", { class: "meta" }, h("span", { class: "badge kind" }, p.type), p.unit_volume_ml ? h("span", {}, Math.round(p.unit_volume_ml) + " mL") : null, abvBadge(p)),
      h("div", { class: "meta" }, ratingBadge(p))),
    RETAILERS.map((r) => cell(p, r)));
  const open = () => openDetail(p.id);
  node.addEventListener("click", open);
  node.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
  return node;
}
// "Canberra Airport, Majura (ACT)": where a retailer's prices were collected. Liquorland prices are
// state-wide; the store named is the nearest one the visit picked.
function storeLabel(r, l) {
  const raw = (l.store_name || "").trim();
  if (!raw || /\(state pricing\)$/i.test(raw)) return `state-wide pricing (${l.state})`;
  const escaped = r.name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  let name = raw.replace(new RegExp("^" + escaped + "\\s+", "i"), "") || raw;
  if (l.suburb && !name.toLowerCase().includes(l.suburb.toLowerCase())) name += ", " + l.suburb;
  return r.id === "liquorland" ? `${name} (${l.state}, state-wide prices)` : `${name} (${l.state})`;
}
function renderLocations(loc) {
  if (loc.postcode) $("state-postcode").textContent = loc.postcode + " (" + loc.state_name + ")";
  $("notices").replaceChildren(...loc.notices.map((n) => h("div", { class: "banner notice" }, n)));
  const parts = RETAILERS.map((r) => {
    const l = loc.retailers[r.id];
    if (!l) return null;
    return `${r.name}: ${storeLabel(r, l)}`;
  }).filter(Boolean);
  $("stores").textContent = parts.length ? "Prices from - " + parts.join(" · ") : "";
}

async function search(append) {
  if (!state_.region) return;
  if (!append) state_.offset = 0;
  try {
    const data = STATIC_MODE
      ? await staticCompare(filterParams(), PAGE, state_.offset)
      : await api("/api/compare", { ...apiParams(filterParams()), limit: PAGE, offset: state_.offset });
    $("demo-banner").hidden = !data.meta.demo;
    renderLocations(data.locations);
    fillFacets(data.meta.facets);
    $("filters").hidden = false;
    const list = $("results");
    if (!append) list.replaceChildren();
    const anyData = Object.values(data.locations.retailers).some(Boolean);
    if (!data.products.length && !append) {
      list.append(h("div", { class: "empty-state" }, anyData
        ? "No products match these filters for this state. Try clearing some filters."
        : `No prices for ${data.locations.state_name} yet.`));
    }
    if (!append) renderSponsored();
    data.products.forEach((p) => list.append(card(p)));
    state_.offset += data.products.length;
    $("more").hidden = state_.offset >= data.meta.total;
    $("summary").textContent = `${data.meta.total} product${data.meta.total === 1 ? "" : "s"} · ${data.locations.state_name}`
      + (data.meta.latest_data ? " · latest data " + ago(data.meta.latest_data) : "");
    const url = new URL(location.href);
    url.search = new URLSearchParams({ state: state_.region }).toString();
    history.replaceState(null, "", url);
  } catch (e) {
    showStateError(e.message);
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
function reviewsSection(p) {
  const rows = RETAILERS.map((r) => {
    const e = p.retailers[r.id];
    const name = h("span", { class: "rname" }, h("span", { class: "dot", style: "background:" + r.color }), r.name);
    if (!e) return h("tr", {}, h("td", {}, name), h("td", { colspan: 3, class: "sub" }, "Not listed"));
    if (!e.rating) {
      return h("tr", {}, h("td", {}, name), h("td", { colspan: 3, class: "sub" },
        e.review_count === 0 ? "No reviews yet" : "Ratings not available from this retailer"));
    }
    return h("tr", {}, h("td", {}, name), h("td", {}, stars(e.rating)),
      h("td", { class: "num" }, e.rating.toFixed(1)),
      h("td", { class: "num" }, e.review_count ? e.review_count + " review" + (e.review_count === 1 ? "" : "s") : ""));
  });
  const head = p.rating
    ? h("div", { class: "big" }, h("span", { class: "num" }, p.rating.toFixed(1)), stars(p.rating),
      h("span", { class: "sub" }, p.review_count ? `average of ${p.review_count} review${p.review_count === 1 ? "" : "s"}` : "average rating"))
    : h("p", { class: "sub" }, "No ratings yet for this product.");
  return h("div", { class: "reviews" }, head,
    h("div", { class: "tbl" }, h("table", {}, h("tbody", {}, rows))),
    h("p", { class: "sub" }, "Average ratings as shown by each retailer, pooled by number of reviews."));
}
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
    h("div", { class: "meta" }, h("span", { class: "badge kind" }, p.type), p.unit_volume_ml ? h("span", {}, Math.round(p.unit_volume_ml) + " mL") : null, abvBadge(p)),
    h("div", { class: "meta" }, ratingBadge(p)),
    h("h3", {}, "Prices"), table,
    h("h3", {}, "Price alert"), alertBox(p),
    h("h3", {}, "Reviews"), reviewsSection(p),
    h("h3", {}, "Price history"),
    d.demo ? h("p", { class: "banner demo" }, "Demo data: this history is simulated.") : null,
    keys.length ? [select, chartBox, info] : h("p", { class: "sub" }, "No history yet."));
  if (keys.length) draw(defaultKey);
  return root;
}
async function openDetail(id) {
  try {
    const d = STATIC_MODE ? await staticDetail(id) : await api("/api/products/" + id, { state: state_.region });
    $("detail-body").replaceChildren(detailBody(d));
    $("detail").showModal();
  } catch (e) { alert(e.message); }
}


// ---- Age gate ---------------------------------------------------------------
// Alcohol content: ask once per device. The answer stays in localStorage only.
function initAgeGate() {
  const dlg = $("agegate");
  if (store("beeroo.age18") === "1" || typeof dlg.showModal !== "function") return;
  dlg.addEventListener("cancel", (e) => e.preventDefault());       // Escape must not skip it
  $("age-yes").addEventListener("click", () => { save("beeroo.age18", "1"); dlg.close(); });
  $("age-no").addEventListener("click", () => {
    $("agegate-title").textContent = "Sorry, you can't use Beeroo";
    $("agegate-text").replaceChildren("You must be 18 or over to view alcohol prices. If you want to talk to someone about alcohol, visit ",
      h("a", { href: "https://drinkwise.org.au", rel: "noopener" }, "drinkwise.org.au"), ".");
    $("age-yes").hidden = true; $("age-no").hidden = true;
  });
  dlg.showModal();
}

// ---- Sponsored placement ----------------------------------------------------
// sponsored.json lists paid placements. One is shown above the list, labelled, and never affects ranking.
let sponsoredConfig = { placements: [] };
const today = () => new Date().toISOString().slice(0, 10);
function eligiblePlacement(cfg, region, day) {
  return (cfg.placements || []).find((x) =>
    (!x.states || !x.states.length || x.states.includes(region))
    && (!x.starts || x.starts <= day) && (!x.ends || day <= x.ends)
    && /^(https:|mailto:)/.test(x.url || ""));
}
function renderSponsored() {
  const x = eligiblePlacement(sponsoredConfig, state_.region, today());
  if (!x) return $("sponsored").replaceChildren();
  $("sponsored").replaceChildren(h("aside", { class: "sponsored", "aria-label": "Sponsored" },
    h("div", { class: "tagline" },
      h("div", { class: "label" }, "Sponsored" + (x.example ? " · example placement" : "") + " · " + x.sponsor),
      h("h2", {}, x.headline), h("p", {}, x.body)),
    h("a", { class: "cta", href: x.url, rel: "sponsored noopener noreferrer", target: x.url.startsWith("https:") ? "_blank" : null }, x.cta || "Learn more"),
    h("p", { class: "fine sub" }, "Paid placement. It doesn't change the price rankings below. 18+ only. Drink responsibly.")));
}
async function loadSponsored() {
  try { sponsoredConfig = await loadJson("sponsored.json"); } catch { sponsoredConfig = { placements: [] }; }
}

// ---- Price alerts -----------------------------------------------------------
// Needs the /api/alerts function (functions/ folder, Cloudflare Pages). Without it the form says so politely.
function alertPacks(p) {
  const seen = new Map();
  for (const e of Object.values(p.retailers)) {
    for (const o of e.options) {
      const key = o.pack_type + ":" + o.units;
      if (!seen.has(key)) seen.set(key, o.label);
    }
  }
  return [...seen].sort((a, b) => a[0].split(":")[1] - b[0].split(":")[1]);
}
function alertBox(p) {
  const packs = alertPacks(p);
  if (!STATIC_MODE || !packs.length) return h("p", { class: "sub" }, "Price alerts aren't available here.");
  const msg = h("p", { class: "msg", role: "status" });
  const pack = h("select", { id: "alert-pack", "aria-label": "Pack size" }, packs.map(([k, label]) => h("option", { value: k }, label)));
  const target = h("input", { id: "alert-target", type: "number", min: "1", max: "999", step: "0.01", inputmode: "decimal", placeholder: "Any new low" });
  const email = h("input", { id: "alert-email", type: "email", required: true, autocomplete: "email", maxlength: "254", value: store("beeroo.email") || "" });
  const trap = h("input", { name: "website", tabindex: "-1", autocomplete: "off", "aria-hidden": "true" });
  const go = h("button", { type: "submit" }, "Email me");
  const form = h("form", {},
    h("label", {}, "Pack", pack),
    h("label", {}, "Alert me at or below ($)", target),
    h("label", {}, "Your email", email),
    h("div", { class: "hp" }, trap), go, msg);
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    go.disabled = true; msg.className = "msg"; msg.textContent = "Saving…";
    try {
      const res = await fetch("/api/alerts", { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ email: email.value.trim(), product_id: p.id, state: state_.region, pack: pack.value,
          target: target.value ? Number(target.value) : null, website: trap.value }) });
      const out = await res.json().catch(() => ({}));
      if (res.status === 404) throw new Error("Price alerts aren't switched on for this site yet.");
      if (!res.ok) throw new Error(out.error || "Couldn't save the alert. Please try again.");
      save("beeroo.email", email.value.trim());
      msg.className = "msg ok"; msg.textContent = out.message || "Alert saved.";
    } catch (e) {
      msg.className = "msg bad"; msg.textContent = e.message;
    } finally { go.disabled = false; }
  });
  return h("div", { class: "alertbox" },
    h("p", { class: "sub" }, "We'll email you when this beer drops to your price at any retailer in " + state_.region + ", based on the prices we collect each day. Leave the price blank to be told when it hits its lowest price we've recorded. Free: up to 3 alerts."),
    form,
    h("p", { class: "sub" }, "We'll only email about your alerts, and every email has a one-click unsubscribe. See the ", h("a", { href: "privacy.html" }, "privacy policy"), "."));
}

// ---- Wiring -----------------------------------------------------------------
function showStateError(msg) {
  const el = $("state-error");
  el.textContent = msg; el.hidden = !msg;
}
function chooseState(code) {
  showStateError("");
  state_.region = code;
  if (code) save("beeroo.state", code);
  if (!code) { $("results").replaceChildren(); $("summary").textContent = ""; $("filters").hidden = true; return; }
  search(false);
}
$("state").addEventListener("change", () => chooseState($("state").value));
$("more").addEventListener("click", () => search(true));
$("detail-close").addEventListener("click", () => $("detail").close());
$("detail").addEventListener("click", (e) => { if (e.target === $("detail")) $("detail").close(); });
let timer;
$("q").addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => search(false), 300); });
$("reverse").addEventListener("click", () => {
  const on = $("reverse").getAttribute("aria-pressed") !== "true";
  $("reverse").setAttribute("aria-pressed", String(on));
  search(false);
});
for (const id of ["sort", "min_units", "max_units", "min_abv", "max_abv", "include_member", "multi", "best_only"]) {
  $(id).addEventListener("change", () => search(false));
}

// ---- Filter widgets -----------------------------------------------------------
function setOptions(select, first, options) {
  const keep = select.value;
  select.replaceChildren(h("option", { value: "" }, first), ...options.map(([value, label]) => h("option", { value }, label)));
  if (options.some(([value]) => String(value) === keep)) select.value = keep;
}
function selectedTypes() {
  return [...document.querySelectorAll("#type-options input:checked")].map((i) => i.value);
}
function typeSummary() {
  const on = selectedTypes();
  $("type-summary").textContent = !on.length ? "All types" : on.length <= 2 ? on.join(", ") : on.length + " types";
}
function fillFacets(facets) {
  if (!facets) return;
  const keep = new Set(selectedTypes());
  $("type-options").replaceChildren(...facets.types.map((t) => {
    const box = h("input", { type: "checkbox", value: t.type, checked: keep.has(t.type) });
    box.addEventListener("change", () => { typeSummary(); search(false); });
    return h("label", { class: "opt" }, box, t.type, h("span", { class: "count" }, "(" + t.count + ")"));
  }));
  typeSummary();
  const units = facets.units.map((u) => [u, u === 1 ? "1 (single)" : String(u)]);
  setOptions($("min_units"), "Min: any", units);
  setOptions($("max_units"), "Max: any", units);
}
function buildStaticFilters() {
  const abv = ABV_STEPS.map((a) => [a, a + "%"]);
  setOptions($("min_abv"), "Min: any", abv);
  setOptions($("max_abv"), "Max: any", abv);
  $("retailer-chips").replaceChildren(...RETAILERS.map((r) => {
    const chip = h("button", { type: "button", class: "chip", "aria-pressed": "false", "data-retailer": r.id },
      h("span", { class: "dot", style: "background:" + r.color }), r.name);
    chip.addEventListener("click", () => {
      const on = !state_.retailers.has(r.id);
      on ? state_.retailers.add(r.id) : state_.retailers.delete(r.id);
      chip.setAttribute("aria-pressed", String(on));
      $("best_only").disabled = state_.retailers.size === 0;      // "best price at a picked retailer" needs a pick
      if ($("best_only").disabled) $("best_only").checked = false;
      search(false);
    });
    return chip;
  }));
  $("type-clear").addEventListener("click", () => {
    document.querySelectorAll("#type-options input").forEach((i) => { i.checked = false; });
    typeSummary();
    search(false);
  });
  // the type list closes when you click elsewhere or press Escape
  document.addEventListener("click", (e) => { if (!$("type").contains(e.target)) $("type").open = false; });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("type").open = false; });
  $("clear").addEventListener("click", () => {
    $("q").value = "";
    for (const id of ["min_units", "max_units", "min_abv", "max_abv"]) $(id).value = "";
    document.querySelectorAll("#type-options input").forEach((i) => { i.checked = false; });
    typeSummary();
    $("sort").value = "value";
    $("reverse").setAttribute("aria-pressed", "false");
    $("include_member").checked = true;
    $("multi").checked = false;
    $("best_only").checked = false;
    $("best_only").disabled = true;
    state_.retailers.clear();
    document.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", "false"));
    search(false);
  });
}

async function loadStates() {
  if (STATIC_MODE) {
    const manifest = await loadJson("data/manifest.json");
    return manifest.all_states.map((s) => ({ ...s, covered: manifest.states.includes(s.code) }));
  }
  return (await api("/api/states")).states;
}
async function init() {
  initAgeGate();
  buildStaticFilters();
  await loadSponsored();
  try {
    state_.states = await loadStates();
  } catch (e) { return showStateError("Couldn't load the list of states. Please reload."); }
  const select = $("state");
  for (const s of state_.states) {
    select.append(h("option", { value: s.code }, s.name + (s.covered === false ? " (no prices yet)" : "")));
  }
  const fromUrl = (new URLSearchParams(location.search).get("state") || "").toUpperCase();
  const initial = state_.states.some((s) => s.code === fromUrl) ? fromUrl : store("beeroo.state");
  if (initial && state_.states.some((s) => s.code === initial)) { select.value = initial; chooseState(initial); }
}
init();
