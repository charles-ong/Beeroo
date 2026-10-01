// Core decision logic. Pure of Chrome APIs: all I/O is injected via `deps`,
// so it can be unit-tested in Node.
import { classify } from "./matchers.js";
import { sanitizePayload, extractLocation } from "./sanitize.js";
import { looksLoggedOut } from "./loggedout.js";
import { buildContribution, hashString } from "./contribution.js";

export const MAX_PER_HOUR = 20;       // stay under the server's 30/hour limit
export const DEDUPE_MS = 24 * 3600e3;
export const PENDING_MS = 30e3;       // how long to wait for a store response

/**
 * deps: {
 *   getSettings(): {consented, paused, serverUrl}
 *   getInstallId(): string
 *   getLocation(tabId, retailer) / setLocation(tabId, retailer, loc)
 *   getPending(tabId) / setPending(tabId, list)
 *   getSeen() / setSeen(map)         // payload-hash -> timestamp
 *   getSendTimes() / setSendTimes(list)
 *   send(body): Promise<{status, body}>
 *   log(entry)
 *   diag?(event)                     // every decision, so users can see WHY nothing was sent
 *   now(): ms, version: string
 * }
 */
export function createPipeline(deps) {
  const skip = (reason, extra = {}) => ({ action: "skip", reason, ...extra });
  const diag = (c, reason, extra = {}) => {
    if (c && deps.diag) deps.diag({ retailer: c.retailer, role: c.role, reason, t: deps.now(), ...extra });
  };

  async function submit({ tabId, retailer, kind, payload, location }, c = { retailer, role: "products" }) {
    const key = hashString(JSON.stringify({ kind, payload, location }));
    const now = deps.now();

    const seen = await deps.getSeen();
    if (seen[key] && now - seen[key] < DEDUPE_MS) { diag(c, "already_sent"); return skip("already_sent"); }

    const times = (await deps.getSendTimes()).filter((t) => now - t < 3600e3);
    if (times.length >= MAX_PER_HOUR) { diag(c, "rate_limited"); return skip("rate_limited"); }

    const body = buildContribution({
      installId: await deps.getInstallId(), extensionVersion: deps.version, kind, location, payload,
    });
    let res;
    try {
      res = await deps.send(body);
    } catch (e) {
      await deps.log({ t: now, retailer, kind, ok: false, detail: "network error" });
      diag(c, "network_error");
      return { action: "error", reason: "network" };
    }

    times.push(now);
    await deps.setSendTimes(times);

    const ok = res.status === 200 || res.status === 202;
    if (ok) {
      seen[key] = now;
      for (const k of Object.keys(seen)) if (now - seen[k] > DEDUPE_MS) delete seen[k];
      await deps.setSeen(seen);
    }
    await deps.log({
      t: now, retailer, kind, ok, status: res.status,
      products: res.body?.products, detail: ok ? res.body?.status : res.body?.detail,
      store: location ? `${location.store_name || location.store_id} (${location.state})` : undefined,
    });
    diag(c, ok ? "sent" : "rejected", { status: res.status });
    return { action: ok ? "sent" : "rejected", status: res.status };
  }

  async function handle({ tabId, url, method, requestBody, json, texts }) {
    const c = classify(url, method, requestBody);
    if (!c) return skip("ignored");

    const settings = await deps.getSettings();
    const note = (reason, extra) => { diag(c, reason, extra); return skip(reason); };
    if (!settings.consented) return note("no_consent");
    if (settings.paused) return note("paused");

    if (c.role === "location") {
      const loc = extractLocation(c.retailer, c.path, json);
      if (!loc) return note("location_unusable");
      await deps.setLocation(tabId, c.retailer, loc);
      diag(c, "store_seen", { store: `${loc.store_name || loc.store_id} (${loc.state})` });

      // flush any product lists that were waiting for the store
      const pending = (await deps.getPending(tabId)) || [];
      const now = deps.now();
      const keep = [];
      const results = [];
      for (const p of pending) {
        if (p.retailer !== c.retailer) { keep.push(p); continue; }
        if (now - p.t > PENDING_MS) continue;
        results.push(await submit({ tabId, retailer: p.retailer, kind: p.kind, payload: p.payload, location: loc }));
      }
      await deps.setPending(tabId, keep);
      return { action: "location_set", location: loc, flushed: results.length };
    }

    if (!c.beer) return note("not_beer");

    // The user can vouch for a site when automatic detection can't see its header.
    const vouched = settings.assumeLoggedOut && settings.assumeLoggedOut[c.retailer];
    if (!vouched && !looksLoggedOut(texts)) {
      return note("maybe_logged_in", { texts: (texts || []).slice(0, 15) });
    }

    const payload = sanitizePayload(c.kind, json);
    if (!payload) return note("unrecognised_shape");

    if (c.retailer === "liquorland") {
      return submit({ tabId, retailer: c.retailer, kind: c.kind, payload, location: null }, c);
    }

    const location = await deps.getLocation(tabId, c.retailer);
    if (!location) {
      const pending = ((await deps.getPending(tabId)) || []).filter((p) => deps.now() - p.t <= PENDING_MS);
      pending.push({ retailer: c.retailer, kind: c.kind, payload, t: deps.now() });
      await deps.setPending(tabId, pending.slice(-5));
      diag(c, "waiting_for_store");
      return { action: "waiting_for_store" };
    }
    return submit({ tabId, retailer: c.retailer, kind: c.kind, payload, location }, c);
  }

  return { handle };
}
