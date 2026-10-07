// Usage: node static_parity.cjs <siteDir> <casesJson>
// Applies the browser-side query logic to the exported files for each case and
// prints [{name, total, ids, best}] so Python can compare with the server's answer.
const fs = require("fs");
const path = require("path");
const S = require("../../app/static/staticdata.js");

const [siteDir, casesFile] = process.argv.slice(2);
const cases = JSON.parse(fs.readFileSync(casesFile, "utf8"));
const out = cases.map((c) => {
  const file = path.join(siteDir, S.variantPath(c.state));
  const payload = JSON.parse(fs.readFileSync(file, "utf8"));
  const r = S.applyQuery(payload, { ...c.query, limit: 1000, offset: 0 });
  // the derived fields the cards show, so the recomputation is compared too, not just the ids
  const best = r.products.map((p) => [p.id, p.best_value_retailer, p.best_unit_price_retailer,
    p.min_price_per_standard_drink, p.min_unit_price,
    Object.keys(p.retailers).sort().map((k) => [k, p.retailers[k].options.length, p.retailers[k].last_updated, p.retailers[k].stale,
      p.retailers[k].best_value && p.retailers[k].best_value.price, p.retailers[k].cheapest_unit && p.retailers[k].cheapest_unit.price])]);
  return { name: c.name, total: r.meta.total, ids: r.products.map((p) => p.id), best };
});
process.stdout.write(JSON.stringify({ out }));
