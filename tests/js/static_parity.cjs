// Usage: node static_parity.cjs <siteDir> <casesJson>
// Applies the browser-side query logic to the exported files for each case and
// prints [{name, total, ids}] so Python can compare with the server's answer.
const fs = require("fs");
const path = require("path");
const S = require("../../app/static/staticdata.js");

const [siteDir, casesFile] = process.argv.slice(2);
const cases = JSON.parse(fs.readFileSync(casesFile, "utf8"));
const out = cases.map((c) => {
  const state = S.stateForPostcode(c.postcode);
  const file = path.join(siteDir, S.variantPath(state, c.include_member, c.pack));
  const payload = JSON.parse(fs.readFileSync(file, "utf8"));
  const r = S.applyQuery(payload, { ...c.query, limit: 1000, offset: 0 });
  return { name: c.name, total: r.meta.total, ids: r.products.map((p) => p.id) };
});
const postcodes = JSON.parse(process.argv[4] || "[]");
process.stdout.write(JSON.stringify({ out, states: postcodes.map((p) => S.stateForPostcode(p)) }));
