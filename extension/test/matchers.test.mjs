import test from "node:test";
import assert from "node:assert/strict";
import { classify } from "../lib/matchers.js";

const BROWSE = "https://api.danmurphys.com.au/apis/ui/Browse";

test("Dan Murphy's Browse POST for beer is a beer product list", () => {
  const c = classify(BROWSE, "POST", JSON.stringify({ department: "beer", pageNumber: 2 }));
  assert.deepEqual([c.retailer, c.role, c.kind, c.beer], ["dan_murphys", "products", "dan_murphys_browse", true]);
});

test("non-beer departments are recognised as not beer", () => {
  assert.equal(classify(BROWSE, "POST", JSON.stringify({ department: "wine" })).beer, false);
  assert.equal(classify(BROWSE, "POST", "not json").beer, false);
  assert.equal(classify(BROWSE, "POST", null).beer, false);
});

test("wrong verbs are ignored", () => {
  assert.equal(classify(BROWSE, "GET"), null);
  assert.equal(classify("https://api.bws.com.au/apis/ui/ProductGroup/Products/beer_bestsellers", "POST"), null);
});

test("BWS product groups and store responses", () => {
  const p = classify("https://api.bws.com.au/apis/ui/ProductGroup/Products/beer_bestsellers?x=1", "GET");
  assert.deepEqual([p.role, p.kind, p.beer], ["products", "bws_products", true]);
  assert.equal(classify("https://api.bws.com.au/apis/ui/ProductGroup/Products/wine-reds", "GET").beer, false);
  assert.equal(classify("https://api.bws.com.au/apis/ui/Address/SetPickupByStoreNo", "GET").role, "location");
  assert.equal(classify("https://api.bws.com.au/apis/ui/StoreLocator/Store", "GET").role, "location");
});

test("store SEARCH results are not treated as the selected store", () => {
  assert.equal(classify("https://api.bws.com.au/apis/ui/StoreLocator/Stores/bws", "GET"), null);
  assert.equal(classify("https://api.bws.com.au/apis/ui/StoreLocator/Suburbs", "GET"), null);
});

test("Liquorland category lists, but not product detail or other APIs", () => {
  const list = classify("https://www.liquorland.com.au/api/products/ll_act/beer-and-cider", "GET");
  assert.deepEqual([list.kind, list.beer], ["liquorland_products", true]);
  assert.equal(classify("https://www.liquorland.com.au/api/products/ll_wa/wine", "GET").beer, false);
  assert.equal(classify("https://www.liquorland.com.au/api/products/ll_act/beer-and-cider/3813708_ea", "GET"), null);
  assert.equal(classify("https://www.liquorland.com.au/api/auth/ll/anonymous_access_token", "GET"), null);
  assert.equal(classify("https://www.liquorland.com.au/api/order/ll/carts/abc", "GET"), null);
});

test("look-alike and unrelated hosts are ignored", () => {
  for (const host of ["danmurphys.com.au.evil.com", "notbws.com.au", "evil.com", "liquorland.com"]) {
    assert.equal(classify(`https://${host}/apis/ui/Browse`, "POST", '{"department":"beer"}'), null, host);
  }
  assert.equal(classify("not a url", "GET"), null);
});
