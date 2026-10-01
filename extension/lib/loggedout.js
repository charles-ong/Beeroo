// Best-effort check that the visitor is NOT logged in. Fails closed: if we
// can't positively see a "log in" control (and no "log out"), we assume the
// user may be logged in and contribute nothing.
//
// Real example (Dan Murphy's header, logged out): the nav item reads
// "Login My Dan's Account", and the "Login" word sits in its own <span>.

const LOGIN = /^(log\s?in|sign\s?in)\b/i;
const LOGOUT = /\b(log\s?out|sign\s?out)\b/i;

/** @param {string[]} texts short visible texts from the top of the page */
export function looksLoggedOut(texts) {
  const clean = (texts || [])
    .map((t) => String(t).replace(/\s+/g, " ").trim())
    .filter((t) => t && t.length <= 40);
  if (clean.some((t) => LOGOUT.test(t))) return false;
  return clean.some((t) => LOGIN.test(t));
}
