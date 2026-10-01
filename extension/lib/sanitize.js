// Allowlist sanitisers: copy ONLY the fields the server parsers need.
// Anything not listed here (cart state, wishlist flags, recommendations,
// descriptions, images, delivery addresses...) never leaves the browser.

const STATES = new Set(["NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT"]);

const pick = (obj, keys) => {
  const out = {};
  for (const k of keys) if (obj && obj[k] !== undefined) out[k] = obj[k];
  return out;
};

const DM_DETAILS = new Set(["webbrandname", "webtitle", "webalcoholpercentage", "webliquorsize", "webproducttype"]);
const BWS_DETAILS = new Set(["productunitquantity", "alcohol%", "liquorsize", "brand_name"]);

function details(list, allowed) {
  return (Array.isArray(list) ? list : [])
    .filter((d) => d && allowed.has(d.Name))
    .map((d) => ({ Name: d.Name, Value: d.Value }));
}

function sanitizeDanMurphys(json) {
  if (!json || !Array.isArray(json.Bundles)) return null;
  const priceKeys = ["Message", "Value", "PackType", "Quantity", "IsMemberOffer", "PromotionType"];
  return {
    TotalRecordCount: json.TotalRecordCount,
    Bundles: json.Bundles.map((b) => ({
      Products: (b.Products || []).map((p) => ({
        ...pick(p, ["Stockcode", "UrlFriendlyName", "PackageSize", "Description"]),
        Prices: Object.fromEntries(
          ["caseprice", "singleprice", "promoprice"]
            .filter((k) => p.Prices && p.Prices[k])
            .map((k) => [k, pick(p.Prices[k], priceKeys)])
        ),
        AvailablePackTypes: (p.AvailablePackTypes || []).map((a) => pick(a, ["Key", "UnitQty"])),
        AdditionalDetails: details(p.AdditionalDetails, DM_DETAILS),
      })),
    })),
  };
}

function sanitizeBws(json) {
  if (!json || !Array.isArray(json.Items)) return null;
  return {
    TotalRecordCount: json.TotalRecordCount,
    Items: json.Items.map((item) => ({
      ...pick(item, ["PackParentStockCode", "Name"]),
      Products: (item.Products || []).map((p) => ({
        ...pick(p, ["Stockcode", "Price", "Name", "UrlFriendlyName", "IsAvailable", "PackageSize", "BrandName", "PromotionType"]),
        FixedPricePromoTag: pick(p.FixedPricePromoTag, ["PromotionalPrice", "ProductMultiplier"]),
        AdditionalDetails: details(p.AdditionalDetails, BWS_DETAILS),
      })),
    })),
  };
}

function sanitizeLiquorland(json) {
  if (!json || !Array.isArray(json.products)) return null;
  const site = String(json.debugQuery || "").match(/sitestate=(ll_[a-z]+)/);
  if (!site) return null;
  return {
    debugQuery: `sitestate=${site[1]}`, // drop the rest (timestamps, navigation state)
    products: json.products.map((p) => ({
      ...pick(p, ["id", "name", "brand", "isAvailable", "volumeMl", "unitOfMeasure", "productUrl"]),
      price: pick(p.price, ["current", "normal", "acrossAnySix", "memberOnlyPrice"]),
      promotion: pick(p.promotion, ["calloutText"]),
    })),
  };
}

const SANITIZERS = {
  dan_murphys_browse: sanitizeDanMurphys,
  bws_products: sanitizeBws,
  liquorland_products: sanitizeLiquorland,
};

/** @returns sanitised copy, or null when the response isn't the expected shape */
export function sanitizePayload(kind, json) {
  const fn = SANITIZERS[kind];
  try { return fn ? fn(json) : null; } catch { return null; }
}

const titleCase = (s) =>
  String(s).toLowerCase().replace(/(^|[\s'-])([a-z])/g, (_, a, b) => a + b.toUpperCase());

function validLocation(raw) {
  const loc = {
    store_id: String(raw.store_id ?? "").trim(),
    store_name: raw.store_name ? String(raw.store_name).slice(0, 80) : undefined,
    suburb: raw.suburb ? titleCase(raw.suburb).slice(0, 80) : undefined,
    state: String(raw.state ?? "").toUpperCase(),
    postcode: String(raw.postcode ?? "").trim(),
  };
  if (!/^[A-Za-z0-9_-]{1,20}$/.test(loc.store_id)) return null;
  if (!STATES.has(loc.state)) return null;
  if (!/^\d{4}$/.test(loc.postcode)) return null;
  return loc;
}

/**
 * The STORE's details from a location response. Reads only the
 * click-and-collect store block; never delivery addresses.
 */
export function extractLocation(retailer, path, json) {
  try {
    if (retailer === "dan_murphys") {
      const d = json?.ClickAndCollectDetails;
      if (!d || !/click/i.test(json.FulfilmentMethod || "")) return null;
      return validLocation({
        store_id: d.FulfilmentStoreID, store_name: d.FulfilmentStoreName,
        suburb: d.AddressSuburb, state: d.AddressState, postcode: d.AddressPostalCode,
      });
    }
    if (retailer === "bws") {
      if (/SetPickupByStoreNo$/.test(path)) {
        const d = json?.FulfilmentInfo?.ClickAndCollectDetails;
        if (!json?.Success || !d) return null;
        return validLocation({
          store_id: d.FulfilmentStoreID, store_name: d.FulfilmentStoreName,
          suburb: d.AddressSuburb, state: d.AddressState, postcode: d.AddressPostalCode,
        });
      }
      return validLocation({
        store_id: json?.StoreNo, store_name: json?.Name, suburb: json?.Suburb,
        state: json?.State, postcode: json?.Postcode,
      });
    }
  } catch { /* fall through */ }
  return null;
}
