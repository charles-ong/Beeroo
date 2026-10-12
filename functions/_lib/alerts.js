// Shared helpers for the price-alert endpoints (Cloudflare Pages Functions, KV binding "ALERTS").
// KV layout:   alert:<id>  -> the alert        tok:<token> -> <id>
//              byemail:<sha256(email)>:<id> -> ""      confirmed:<sha256(email)> -> "1"      premium:<sha256(email)> -> "1"
export const STATES = ["ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC", "WA"];
export const FREE_LIMIT = 3;
export const PREMIUM_LIMIT = 100;
export const PENDING_TTL = 60 * 60 * 24 * 3;   // an unconfirmed alert disappears after 3 days

export const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", "cache-control": "no-store" } });

export const page = (title, text) =>
  new Response(`<!doctype html><html lang="en-AU"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${title}</title>` +
    `<link rel="stylesheet" href="/static/style.css"><main class="legal"><h1>${title}</h1><p>${text}</p><p><a href="/">Back to Beeroo</a></p></main>`,
    { status: 200, headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" } });

export const hex = (bytes) => [...new Uint8Array(bytes)].map((b) => b.toString(16).padStart(2, "0")).join("");
export const randomHex = (n) => hex(crypto.getRandomValues(new Uint8Array(n)));
export async function sha256(text) { return hex(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text))); }

export function validate(b) {
  const email = String(b.email || "").trim().toLowerCase();
  if (!/^[^\s@]{1,64}@[^\s@]{1,255}\.[^\s@]{2,}$/.test(email) || email.length > 254) return { error: "Enter a valid email address." };
  const productId = Number(b.product_id);
  if (!Number.isInteger(productId) || productId < 1) return { error: "Unknown beer." };
  const state = String(b.state || "").toUpperCase();
  if (!STATES.includes(state)) return { error: "Choose a state first." };
  const pack = String(b.pack || "");
  if (!/^[a-z_]{1,20}:\d{1,3}$/.test(pack)) return { error: "Choose a pack size." };
  let target = null;
  if (b.target !== null && b.target !== undefined && b.target !== "") {
    target = Math.round(Number(b.target) * 100) / 100;
    if (!(target > 0 && target < 1000)) return { error: "Enter a price between $0.01 and $999." };
  }
  return { value: { email, productId, state, pack, target } };
}

// Constant-time-ish string compare for the admin token.
export function sameToken(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length || !a.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}
export function isAdmin(request, env) {
  const h = request.headers.get("authorization") || "";
  return sameToken(h.startsWith("Bearer ") ? h.slice(7) : "", env.ALERTS_ADMIN_TOKEN || "");
}

export async function sendMail(env, to, subject, text) {
  if (!env.RESEND_API_KEY || !env.ALERTS_FROM) throw new Error("email isn't configured");
  const res = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: { authorization: "Bearer " + env.RESEND_API_KEY, "content-type": "application/json" },
    body: JSON.stringify({ from: env.ALERTS_FROM, to: [to], subject, text }),
  });
  if (!res.ok) throw new Error("email failed: " + res.status);
}

export async function listAll(kv, prefix) {
  const keys = [];
  let cursor;
  do {
    const r = await kv.list({ prefix, cursor });
    keys.push(...r.keys);
    cursor = r.list_complete ? undefined : r.cursor;
  } while (cursor);
  return keys;
}

export async function removeAlert(kv, alert) {
  const eh = await sha256(alert.email);
  await Promise.all([kv.delete("alert:" + alert.id), kv.delete("tok:" + alert.token), kv.delete(`byemail:${eh}:${alert.id}`)]);
}
