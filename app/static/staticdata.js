"use strict";
// Client-side equivalent of the server's /api/compare for the FREE static site.
// The exporter writes ONE file per state with every product and every price option;
// this file narrows them (member offers, pack-size range), redoes the derived
// "best price" fields, applies the product filters and sorts, exactly as
// app/queries.py does (a parity test enforces that).
(function (root) {
  function variantPath(state) {
    return `data/${state}/products.json`;
  }

  function detailPath(state, productId) {
    return `data/${state}/p/${productId}.json`;
  }

  const INF = Infinity;
  // "Highest rated": ratings with few reviews are pulled toward the prior (same constants as app/queries.py).
  const RATING_PRIOR = 4.0, RATING_PRIOR_WEIGHT = 5;
  const ratingScore = (p) => {
    const votes = p.review_count || 1;
    return (p.rating * votes + RATING_PRIOR * RATING_PRIOR_WEIGHT) / (votes + RATING_PRIOR_WEIGHT);
  };
  const lt = (a, b) => (a < b ? -1 : a > b ? 1 : 0);

  // Same ordering as queries.ordered: unknown values last (whichever way round), then by name.
  // [what to sort on, natural direction: 1 = low to high, -1 = high to low]
  const NUMERIC_SORTS = {
    value: [(p) => p.min_price_per_standard_drink, 1],
    unit_price: [(p) => p.min_unit_price, 1],
    abv: [(p) => p.abv, -1],
    rating: [(p) => (p.rating ? ratingScore(p) : null), -1],
  };
  const SORT_NAMES = [...Object.keys(NUMERIC_SORTS), "name"];

  function comparator(sort, reverse) {
    if (sort === "name") return (a, b) => (reverse ? -1 : 1) * lt(a.name.toLowerCase(), b.name.toLowerCase());
    const [get, natural] = NUMERIC_SORTS[sort];
    const direction = reverse ? -natural : natural;
    return (a, b) => {
      const x = get(a), y = get(b);
      return lt(x == null, y == null) || (x == null ? 0 : lt(direction * x, direction * y)) || lt(a.name, b.name);
    };
  }

  // First option with the smallest non-null value (Python's min() over the same list).
  function best(options, key) {
    let pick = null;
    for (const o of options) if (o[key] != null && (pick === null || o[key] < pick[key])) pick = o;
    return pick;
  }

  function finishEntry(entry) {
    entry.options.sort((a, b) => a.units - b.units || Number(a.member_only) - Number(b.member_only));
    entry.best_value = best(entry.options, "price_per_standard_drink");
    entry.cheapest_unit = best(entry.options, "unit_price");
    entry.last_updated = entry.options.reduce((m, o) => (o.observed_at > m ? o.observed_at : m), "");
    entry.stale = entry.options.every((o) => o.stale);
  }

  // Which retailer wins on each metric (needs 2+ to compete); ties go to the earlier retailer id.
  function markBest(product) {
    for (const [metric, field, flag] of [
      ["price_per_standard_drink", "best_value", "best_value"],
      ["unit_price", "cheapest_unit", "best_unit_price"],
    ]) {
      const scored = Object.entries(product.retailers)
        .filter(([, e]) => e[field])
        .map(([r, e]) => [e[field][metric], r]);
      scored.sort((a, b) => lt(a[0], b[0]) || lt(a[1], b[1]));
      product[flag + "_retailer"] = scored.length >= 2 ? scored[0][1] : null;
      product["min_" + metric] = scored.length ? Math.min(...scored.map((s) => s[0])) : null;
    }
  }

  function refilter(products, includeMember, minUnits, maxUnits) {
    const kept = [];
    for (const p of products) {
      const retailers = {};
      for (const [r, entry] of Object.entries(p.retailers)) {
        const options = entry.options.filter((o) =>
          (includeMember || !o.member_only)
          && (minUnits == null || o.units >= minUnits)
          && (maxUnits == null || o.units <= maxUnits));
        if (!options.length) continue;
        const fresh = { ...entry, options: options.slice() };
        finishEntry(fresh);
        retailers[r] = fresh;
      }
      if (!Object.keys(retailers).length) continue;
      const copy = { ...p, retailers };
      markBest(copy);
      kept.push(copy);
    }
    return kept;
  }

  const num = (v) => (v == null || v === "" ? null : Number(v));

  /**
   * @param payload one exported state file {meta, locations, products}
   * @param q {q, sort, reverse, include_member (default true), min_abv, max_abv, min_units, max_units,
   *           min_retailers, retailers: [ids], types: [names], limit, offset}
   * @returns same shape as GET /api/compare
   */
  function applyQuery(payload, q) {
    const sort = q.sort || "value";
    if (!SORT_NAMES.includes(sort)) throw new Error("sort must be one of " + [...SORT_NAMES].sort().join(", "));
    const tokens = String(q.q || "").toLowerCase().split(/\s+/).filter(Boolean);
    const wanted = q.retailers && q.retailers.length ? new Set(q.retailers) : null;
    const wantedTypes = q.types && q.types.length ? new Set(q.types) : null;
    const minRetailers = q.min_retailers || 1;
    const minAbv = num(q.min_abv), maxAbv = num(q.max_abv);

    const products = refilter(payload.products, q.include_member !== false, num(q.min_units), num(q.max_units));
    const result = products.filter((p) => {
      const name = p.raw_name.toLowerCase();
      if (tokens.length && !tokens.every((t) => name.includes(t))) return false;
      const present = Object.keys(p.retailers);
      if (wanted && ![...wanted].every((r) => present.includes(r))) return false;   // sold by EVERY picked retailer
      if (wantedTypes && !wantedTypes.has(p.type)) return false;
      if (present.length < minRetailers) return false;
      if (minAbv !== null || maxAbv !== null) {
        if (p.abv == null) return false;
        if (minAbv !== null && p.abv < minAbv) return false;
        if (maxAbv !== null && p.abv > maxAbv) return false;
      }
      return true;
    });
    result.sort(comparator(sort, !!q.reverse));

    const limit = q.limit || 50, offset = q.offset || 0;
    return {
      meta: { ...payload.meta, total: result.length, limit, offset },
      locations: payload.locations,
      products: result.slice(offset, offset + limit),
    };
  }

  const api = { variantPath, detailPath, applyQuery, refilter };
  root.BeerooStatic = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
