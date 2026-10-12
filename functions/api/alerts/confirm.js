// GET /api/alerts/confirm?t=<token>: turns a pending alert on (and remembers the email is confirmed).
import { page, sha256 } from "../../_lib/alerts.js";

export async function onRequestGet({ request, env }) {
  const t = new URL(request.url).searchParams.get("t") || "";
  const id = /^[0-9a-f]{48}$/.test(t) && env.ALERTS ? await env.ALERTS.get("tok:" + t) : null;
  const raw = id ? await env.ALERTS.get("alert:" + id) : null;
  if (!raw) return page("Link expired", "This confirmation link has expired or was already used. You can set the alert up again on the site.");
  const alert = JSON.parse(raw);
  const eh = await sha256(alert.email);
  alert.status = "active";
  await Promise.all([
    env.ALERTS.put("alert:" + id, JSON.stringify(alert)),            // no expiry: now it's permanent
    env.ALERTS.put("tok:" + t, id),
    env.ALERTS.put(`byemail:${eh}:${id}`, ""),
    env.ALERTS.put("confirmed:" + eh, "1"),
  ]);
  return page("Alert confirmed", "We'll email you when the price is right. Every email has an unsubscribe link.");
}
