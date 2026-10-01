"use strict";
// Client-side equivalent of the server's /api/compare for the FREE static
// site: the exporter writes pre-computed product files per state and per
// (member offers x pack) variant; this file picks the file for a state, and applies search/ABV/retailer filters and sorting exactly
// as app/queries.py does (a parity test enforces that).
(function (root) {
  function variantPath(state, includeMember, pack) {
    return `data/${state}/${includeMember ? 1 : 0}-${pack || "any"}.json`;
  }

  function detailPath(state, productId) {
    return `data/${state}/p/${productId}.json`;
  }

  const INF = Infinity;
  const lt = (a, b) => (a < b ? -1 : a > b ? 1 : 0);

  // Same ordering as queries.compare: unknown values last, then by name.
  const SORTS = {
    value: (a, b) => lt(a.min_price_per_standard_drink == null, b.min_price_per_standard_drink == null)
      || lt(a.min_price_per_standard_drink ?? INF, b.min_price_per_standard_drink ?? INF) || lt(a.name, b.name),
    unit_price: (a, b) => lt(a.min_unit_price == null, b.min_unit_price == null)
      || lt(a.min_unit_price ?? INF, b.min_unit_price ?? INF) || lt(a.name, b.name),
    abv: (a, b) => lt(a.abv == null, b.abv == null) || lt(-(a.abv || 0), -(b.abv || 0)) || lt(a.name, b.name),
    name: (a, b) => lt(a.name.toLowerCase(), b.name.toLowerCase()),
  };

  /**
   * @param payload one exported variant file {meta, locations, products}
   * @param q {q, sort, min_abv, max_abv, min_retailers, retailers, limit, offset}
   * @returns same shape as GET /api/compare
   */
  function applyQuery(payload, q) {
    const sort = q.sort || "value";
    if (!SORTS[sort]) throw new Error("sort must be one of " + Object.keys(SORTS).sort().join(", "));
    const tokens = String(q.q || "").toLowerCase().split(/\s+/).filter(Boolean);
    const wanted = q.retailers && q.retailers.length ? new Set(q.retailers) : null;
    const minRetailers = q.min_retailers || 1;
    const minAbv = q.min_abv == null || q.min_abv === "" ? null : Number(q.min_abv);
    const maxAbv = q.max_abv == null || q.max_abv === "" ? null : Number(q.max_abv);

    const result = payload.products.filter((p) => {
      const name = p.name.toLowerCase();
      if (tokens.length && !tokens.every((t) => name.includes(t))) return false;
      const present = Object.keys(p.retailers);
      if (wanted && !present.some((r) => wanted.has(r))) return false;
      if (present.length < minRetailers) return false;
      if (minAbv !== null || maxAbv !== null) {
        if (p.abv == null) return false;
        if (minAbv !== null && p.abv < minAbv) return false;
        if (maxAbv !== null && p.abv > maxAbv) return false;
      }
      return true;
    });
    result.sort(SORTS[sort]);

    const limit = q.limit || 50, offset = q.offset || 0;
    return {
      meta: { ...payload.meta, total: result.length, limit, offset },
      locations: payload.locations,
      products: result.slice(offset, offset + limit),
    };
  }

  const api = { variantPath, detailPath, applyQuery };
  root.BeerooStatic = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
