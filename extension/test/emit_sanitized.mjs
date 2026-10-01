// Prints full contribution bodies exactly as the extension would build them
// from each fixture, so the Python suite can prove the server accepts them and
// parses the filtered payloads identically to the unfiltered ones.
import { readFileSync } from "node:fs";
import { sanitizePayload, extractLocation } from "../lib/sanitize.js";
import { buildContribution } from "../lib/contribution.js";

const fx = (n) => JSON.parse(readFileSync(new URL(`../../tests/fixtures/${n}`, import.meta.url)));
const id = (n) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const make = (n, kind, file, location) =>
  buildContribution({ installId: id(n), extensionVersion: "0.1.0", kind, location, payload: sanitizePayload(kind, fx(file)) });

const woden = extractLocation("bws", "/apis/ui/Address/SetPickupByStoreNo", fx("bws_2606_set_pickup.json"));
const thornleigh = extractLocation("dan_murphys", "/x", {
  FulfilmentMethod: "Click & Collect",
  ClickAndCollectDetails: { FulfilmentStoreID: "1546", FulfilmentStoreName: "Thornleigh", AddressSuburb: "THORNLEIGH", AddressState: "NSW", AddressPostalCode: "2120" },
});

process.stdout.write(JSON.stringify({
  dan_murphys_browse: [make(1, "dan_murphys_browse", "dan_murphys_browse_page1.json", thornleigh), make(2, "dan_murphys_browse", "dan_murphys_browse_page1.json", thornleigh)],
  bws_products: [make(1, "bws_products", "bws_2606_products.json", woden), make(2, "bws_products", "bws_2606_products.json", woden)],
  liquorland_products: [make(1, "liquorland_products", "liquorland_act_products.json", null), make(2, "liquorland_products", "liquorland_act_products.json", null)],
}));
