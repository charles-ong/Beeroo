export const SCHEMA_VERSION = 1;

export function buildContribution({ installId, extensionVersion, kind, location, payload }) {
  return {
    schema_version: SCHEMA_VERSION,
    install_id: installId,
    extension_version: extensionVersion,
    kind,
    logged_in: false, // we only ever send when we positively saw a logged-out page
    location: location || null,
    payload,
  };
}

export function newInstallId() {
  return crypto.randomUUID();
}

/** Small, stable, non-cryptographic hash for local de-duplication. */
export function hashString(str) {
  let h1 = 0xdeadbeef, h2 = 0x41c6ce57;
  for (let i = 0; i < str.length; i++) {
    const c = str.charCodeAt(i);
    h1 = Math.imul(h1 ^ c, 2654435761);
    h2 = Math.imul(h2 ^ c, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return (4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(36);
}
