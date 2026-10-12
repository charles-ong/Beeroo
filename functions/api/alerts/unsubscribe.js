// GET /api/alerts/unsubscribe?t=<token>: deletes that alert. Works for pending and active alerts.
import { page, removeAlert } from "../../_lib/alerts.js";

export async function onRequestGet({ request, env }) {
  const t = new URL(request.url).searchParams.get("t") || "";
  const id = /^[0-9a-f]{48}$/.test(t) && env.ALERTS ? await env.ALERTS.get("tok:" + t) : null;
  const raw = id ? await env.ALERTS.get("alert:" + id) : null;
  if (raw) await removeAlert(env.ALERTS, JSON.parse(raw));
  return page("Alert removed", "You won't get any more emails for that alert.");   // same answer either way
}
