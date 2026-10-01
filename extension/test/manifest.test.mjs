import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";

const root = new URL("../", import.meta.url);
const manifest = JSON.parse(readFileSync(new URL("manifest.json", root)));

test("minimal permissions: storage only, no broad or sensitive access", () => {
  assert.equal(manifest.manifest_version, 3);
  assert.deepEqual(manifest.permissions, ["storage"]);
  const all = JSON.stringify(manifest);
  for (const bad of ["<all_urls>", "webRequest", "cookies", "history", "tabs\"", "scripting", "downloads", "nativeMessaging", "*://*/*"]) {
    assert.ok(!all.includes(bad), bad);
  }
});

test("host access: local dev servers up front, any https server only on request", () => {
  assert.deepEqual(manifest.host_permissions, ["http://127.0.0.1/*", "http://localhost/*"]);
  assert.deepEqual(manifest.optional_host_permissions, ["https://*/*"]);
});

test("content scripts run only on the three retailers over https", () => {
  const allowed = new Set(["https://www.danmurphys.com.au/*", "https://www.bws.com.au/*", "https://bws.com.au/*", "https://www.liquorland.com.au/*"]);
  assert.equal(manifest.content_scripts.length, 2);
  for (const cs of manifest.content_scripts) for (const m of cs.matches) assert.ok(allowed.has(m), m);
  const main = manifest.content_scripts.filter((c) => c.world === "MAIN");
  assert.equal(main.length, 1);
  assert.deepEqual(main[0].js, ["inject.js"]);
});

test("every referenced file exists", () => {
  const files = [manifest.background.service_worker, manifest.action.default_popup, manifest.options_ui.page,
    ...manifest.content_scripts.flatMap((c) => c.js)];
  for (const f of files) assert.ok(existsSync(new URL(f, root)), f);
  for (const f of ["popup.js", "options.js", "ui.css", "lib/pipeline.js", "lib/sanitize.js"]) assert.ok(existsSync(new URL(f, root)), f);
});

test("no inline scripts in the HTML pages (MV3 CSP)", () => {
  for (const page of ["popup.html", "options.html"]) {
    const html = readFileSync(new URL(page, root), "utf8");
    assert.ok(!/<script(?![^>]*\bsrc=)/i.test(html), page);
    assert.ok(!/\son\w+=/i.test(html), page);
  }
});
