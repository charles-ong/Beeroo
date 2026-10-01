import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { sanitizePayload, extractLocation } from "../lib/sanitize.js";
import { SAMPLE_KIND, SAMPLE_RESPONSE } from "../lib/sample.js";

const fx = (n) => JSON.parse(readFileSync(new URL(`../../tests/fixtures/${n}`, import.meta.url)));

const FORBIDDEN = [
  "QuantityInTrolley", "IsInTrolley", "IsWatched", "IsInWishList", "IsInDefaultList", "RichDescription",
  "RecommendedProducts", "CrossSellDetails", "SupplyLimitMessage", "ImageTag", "SmallImageFile", "Inventory",
  "productcopy", "UniqueSellingProposition", "image", "imageList", "ratings",
];

function keysDeep(o, out = new Set()) {
  if (Array.isArray(o)) o.forEach((x) => keysDeep(x, out));
  else if (o && typeof o === "object") for (const [k, v] of Object.entries(o)) { out.add(k); keysDeep(v, out); }
  return out;
}

for (const [kind, file] of [
  ["dan_murphys_browse", "dan_murphys_browse_page1.json"],
  ["bws_products", "bws_2606_products.json"],
  ["liquorland_products", "liquorland_act_products.json"],
]) {
  test(`${kind}: sanitised payload drops personalised/bulky fields and shrinks`, () => {
    const raw = fx(file);
    const clean = sanitizePayload(kind, raw);
    assert.ok(clean);
    const keys = keysDeep(clean);
    for (const f of FORBIDDEN) assert.ok(!keys.has(f), `${f} leaked`);
    assert.ok(JSON.stringify(clean).length < JSON.stringify(raw).length);
  });
}

test("liquorland keeps only the site state from debugQuery", () => {
  const clean = sanitizePayload("liquorland_products", fx("liquorland_wa_products.json"));
  assert.equal(clean.debugQuery, "sitestate=ll_wa");
});

test("liquorland payloads without a site state are refused", () => {
  assert.equal(sanitizePayload("liquorland_products", { products: [], debugQuery: "x=1" }), null);
});

test("unexpected shapes and unknown kinds return null, never throw", () => {
  assert.equal(sanitizePayload("bws_products", { nope: 1 }), null);
  assert.equal(sanitizePayload("dan_murphys_browse", null), null);
  assert.equal(sanitizePayload("evil", {}), null);
  assert.equal(sanitizePayload("bws_products", { Items: [null] }), null);
});

test("the on-screen sample preview really is filtered", () => {
  const clean = sanitizePayload(SAMPLE_KIND, SAMPLE_RESPONSE);
  const text = JSON.stringify(clean);
  for (const f of ["QuantityInTrolley", "RichDescription", "RecommendedProducts", "productcopy", "marketing"]) {
    assert.ok(!text.includes(f), f);
  }
  assert.ok(text.includes("alcohol%") && text.includes("5.5"));
});

test("BWS pickup location: store only, never address fields", () => {
  const loc = extractLocation("bws", "/apis/ui/Address/SetPickupByStoreNo", fx("bws_2606_set_pickup.json"));
  assert.deepEqual(loc, { store_id: "6723", store_name: "Woden", suburb: "Woden", state: "ACT", postcode: "2606" });
  assert.ok(!JSON.stringify(loc).includes("Westfields"));
});

test("BWS default-store response", () => {
  const loc = extractLocation("bws", "/apis/ui/StoreLocator/Store", {
    StoreNo: "1763", Name: "Umina", Suburb: "Umina Beach", State: "NSW", Postcode: "2257",
    Phone: "(02) 4342 1935", AddressLine1: "Corner of West St",
  });
  assert.deepEqual(loc, { store_id: "1763", store_name: "Umina", suburb: "Umina Beach", state: "NSW", postcode: "2257" });
});

test("Dan Murphy's click-and-collect store; delivery mode yields nothing", () => {
  const prefs = {
    FulfilmentMethod: "Click & Collect",
    ClickAndCollectDetails: { FulfilmentStoreID: "1546", FulfilmentStoreName: "Thornleigh", AddressSuburb: "THORNLEIGH", AddressState: "NSW", AddressPostalCode: "2120", AddressStreet1: "SECRET ST" },
  };
  const loc = extractLocation("dan_murphys", "/apis/ui/Fulfilment/Preferences", prefs);
  assert.deepEqual(loc, { store_id: "1546", store_name: "Thornleigh", suburb: "Thornleigh", state: "NSW", postcode: "2120" });
  assert.ok(!JSON.stringify(loc).includes("SECRET"));
  assert.equal(extractLocation("dan_murphys", "/x", { ...prefs, FulfilmentMethod: "Delivery" }), null);
  assert.equal(extractLocation("dan_murphys", "/x", { FulfilmentMethod: "Click & Collect", ClickAndCollectDetails: null }), null);
});

test("invalid location fields are rejected", () => {
  const bad = (over) => extractLocation("bws", "/apis/ui/StoreLocator/Store", { StoreNo: "1", Name: "N", Suburb: "S", State: "NSW", Postcode: "2000", ...over });
  assert.ok(bad({}));
  assert.equal(bad({ State: "XX" }), null);
  assert.equal(bad({ Postcode: "20" }), null);
  assert.equal(bad({ StoreNo: "a b; drop" }), null);
  assert.equal(bad({ StoreNo: "" }), null);
});
