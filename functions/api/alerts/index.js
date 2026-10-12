// POST /api/alerts  { email, product_id, state, pack, target|null, website (honeypot) }
// Creates an alert. First alert for an email needs a confirmation click (double opt-in); after that they're live at once.
import { FREE_LIMIT, PREMIUM_LIMIT, PENDING_TTL, json, validate, sha256, randomHex, sendMail, listAll } from "../../_lib/alerts.js";

export async function onRequestPost({ request, env }) {
  if (!env.ALERTS) return json({ error: "Price alerts aren't switched on yet." }, 404);
  const origin = request.headers.get("origin");
  if (origin && origin !== new URL(request.url).origin) return json({ error: "Not allowed." }, 403);

  let body;
  try { body = await request.json(); } catch { return json({ error: "Bad request." }, 400); }
  if (body.website) return json({ ok: true, message: "Check your inbox to confirm." });     // honeypot: pretend success
  const v = validate(body);
  if (v.error) return json({ error: v.error }, 400);
  const { email, productId, state, pack, target } = v.value;

  const eh = await sha256(email);
  const [premium, confirmed, existing] = await Promise.all([
    env.ALERTS.get("premium:" + eh), env.ALERTS.get("confirmed:" + eh), listAll(env.ALERTS, `byemail:${eh}:`),
  ]);
  const limit = premium ? PREMIUM_LIMIT : FREE_LIMIT;
  if (existing.length >= limit) {
    return json({ error: premium ? "That's the maximum number of alerts." : `Free accounts can have ${FREE_LIMIT} alerts. Remove one with its email's unsubscribe link, or upgrade.` }, 409);
  }

  const id = randomHex(8), token = randomHex(24);
  const alert = { id, token, email, product_id: productId, state, pack, target, include_member: true,
    status: confirmed ? "active" : "pending", plan: premium ? "premium" : "free", created: new Date().toISOString(), last_notified_price: null };
  const ttl = confirmed ? undefined : { expirationTtl: PENDING_TTL };
  await Promise.all([
    env.ALERTS.put("alert:" + id, JSON.stringify(alert), ttl),
    env.ALERTS.put("tok:" + token, id, ttl),
    env.ALERTS.put(`byemail:${eh}:${id}`, "", ttl),
  ]);

  if (confirmed) return json({ ok: true, message: "Alert saved. We'll email you when the price is right." });
  const site = new URL(request.url).origin;
  try {
    await sendMail(env, email, "Confirm your Beeroo price alert",
      `Someone (hopefully you) asked Beeroo to email ${email} about a price drop.\n\nConfirm: ${site}/api/alerts/confirm?t=${token}\n\n` +
      `If it wasn't you, ignore this email and nothing will be sent. Or delete it now: ${site}/api/alerts/unsubscribe?t=${token}\n`);
  } catch {
    return json({ error: "Couldn't send the confirmation email. Please try again later." }, 502);
  }
  return json({ ok: true, message: "Check your inbox and click the confirmation link to turn this alert on." });
}
