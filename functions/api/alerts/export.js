// GET /api/alerts/export (Authorization: Bearer ALERTS_ADMIN_TOKEN): every active alert, for scripts/send_alerts.py.
import { json, isAdmin, listAll } from "../../_lib/alerts.js";

export async function onRequestGet({ request, env }) {
  if (!env.ALERTS || !isAdmin(request, env)) return json({ error: "forbidden" }, 403);
  const alerts = [];
  for (const k of await listAll(env.ALERTS, "alert:")) {
    const raw = await env.ALERTS.get(k.name);
    const a = raw && JSON.parse(raw);
    if (a && a.status === "active") alerts.push(a);
  }
  return json({ alerts });
}
