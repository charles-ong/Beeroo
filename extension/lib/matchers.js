// Decide whether a network response is one we may read, and what it is.
// Only these exact endpoints are ever considered; everything else is ignored.

const HOSTS = {
  dan_murphys: /(^|\.)danmurphys\.com\.au$/i,
  bws: /(^|\.)bws\.com\.au$/i,
  liquorland: /(^|\.)liquorland\.com\.au$/i,
};

function retailerFor(hostname) {
  return Object.keys(HOSTS).find((r) => HOSTS[r].test(hostname)) || null;
}

function parseBody(requestBody) {
  try { return requestBody ? JSON.parse(requestBody) : null; } catch { return null; }
}

/**
 * @returns null | {retailer, role: 'products'|'location', kind?, beer?: boolean, path}
 */
export function classify(url, method = "GET", requestBody = null) {
  let u;
  try { u = new URL(url); } catch { return null; }

  const retailer = retailerFor(u.hostname);
  if (!retailer) return null;

  const path = decodeURIComponent(u.pathname);
  const verb = String(method).toUpperCase();

  if (retailer === "dan_murphys") {
    if (verb === "POST" && /\/apis\/ui\/Browse$/.test(path)) {
      const body = parseBody(requestBody);
      return { retailer, role: "products", kind: "dan_murphys_browse", beer: body?.department === "beer", path };
    }
    // Location responses are recognised on any HTTP method: the page may GET or
    // POST/PUT when a store is chosen.
    if (/\/apis\/ui\/Fulfilment\/Preferences$/.test(path)) {
      return { retailer, role: "location", path };
    }
  }

  if (retailer === "bws") {
    // SetPickupByStoreNo is a POST in the real site (seen in a captured HAR).
    if (/\/apis\/ui\/Address\/SetPickupByStoreNo$/.test(path) || /\/apis\/ui\/StoreLocator\/Store$/.test(path)) {
      return { retailer, role: "location", path };
    }
    const group = verb === "GET" && path.match(/\/apis\/ui\/ProductGroup\/Products\/([^/]+)\/?$/);
    if (group) {
      return { retailer, role: "products", kind: "bws_products", beer: /^beer/i.test(group[1]), path };
    }
  }

  if (retailer === "liquorland" && verb === "GET") {
    // Real path: /api/products/ll/<state>/<category> (site "ll/act"; the payload says "ll_act")
    const list = path.match(/^\/api\/products\/ll\/([a-z]+)\/([^/]+)\/?$/);
    if (list) {
      return { retailer, role: "products", kind: "liquorland_products", beer: /^beer/i.test(list[2]), path };
    }
  }

  return null;
}
