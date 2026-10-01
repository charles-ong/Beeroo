// Plain-English explanations for every decision the pipeline can make.
export const REASONS = {
  sent: ["ok", "Sent and accepted"],
  store_seen: ["ok", "Store detected"],
  already_sent: ["ok", "Already sent (not sent twice)"],
  no_consent: ["bad", "Contributing is turned off (tick the consent box in settings)"],
  paused: ["warn", "Paused"],
  maybe_logged_in: ["bad", "Couldn't confirm you're logged out, so nothing was sent"],
  not_beer: ["warn", "Not a beer page (ignored)"],
  waiting_for_store: ["warn", "Waiting to learn which store you've chosen"],
  location_unusable: ["bad", "Store unusable (delivery mode, or an unexpected store response)"],
  unrecognised_shape: ["bad", "The site's response had an unexpected format"],
  rate_limited: ["warn", "Hourly limit reached"],
  rejected: ["bad", "The Beeroo server rejected it"],
  network_error: ["bad", "Couldn't reach the Beeroo server (is it running?)"],
};

export const RETAILER_NAMES = { dan_murphys: "Dan Murphy's", bws: "BWS", liquorland: "Liquorland" };

export function explain(reason) {
  return REASONS[reason] || ["warn", reason];
}
