// POST /api/alerts/mark { updates: [{ id, last_notified_price|null }] } (Bearer ALERTS_ADMIN_TOKEN): remembers what was emailed.
import { json, isAdmin } from "../../_lib/alerts.js";

export async function onRequestPost({ request, env }) {
  if (!env.ALERTS || !isAdmin(request, env)) return json({ error: "forbidden" }, 403);
  let body;
  try { body = await request.json(); } catch { return json({ error: "Bad request." }, 400); }
  let n = 0;
  for (const u of Array.isArray(body.updates) ? body.updates : []) {
    if (!/^[0-9a-f]{16}$/.test(String(u.id))) continue;
    const raw = await env.ALERTS.get("alert:" + u.id);
    if (!raw) continue;
    const a = JSON.parse(raw);
    a.last_notified_price = u.last_notified_price == null ? null : Number(u.last_notified_price);
    await env.ALERTS.put("alert:" + u.id, JSON.stringify(a));
    n++;
  }
  return json({ ok: true, updated: n });
}
