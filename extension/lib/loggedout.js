// Best-effort check that the visitor is NOT logged in. Fails closed: if we
// can't positively see a "log in" control (and no "log out"), we assume the
// user may be logged in and contribute nothing.

const LOGIN = /^(log\s?in|sign\s?in)(\s*[\/&]\s*(register|sign\s?up|join))?$/i;
const LOGOUT = /^(log\s?out|sign\s?out)$/i;

/** @param {string[]} texts short visible texts from the page header/nav */
export function looksLoggedOut(texts) {
  const clean = (texts || []).map((t) => String(t).replace(/\s+/g, " ").trim()).filter(Boolean);
  if (clean.some((t) => LOGOUT.test(t))) return false;
  return clean.some((t) => LOGIN.test(t));
}
