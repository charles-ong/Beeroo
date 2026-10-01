import test from "node:test";
import assert from "node:assert/strict";
import { looksLoggedOut } from "../lib/loggedout.js";

test("a visible Login control means logged out", () => {
  assert.equal(looksLoggedOut(["Offers", "Wine", "Login", "My Dan's Account"]), true);
  assert.equal(looksLoggedOut(["Sign in"]), true);
  assert.equal(looksLoggedOut(["Log in / Register"]), true);
});

test("a Log out control means logged in, even if Login text also appears", () => {
  assert.equal(looksLoggedOut(["Log out", "Login"]), false);
  assert.equal(looksLoggedOut(["Sign out"]), false);
});

test("fails closed: unknown page state is treated as possibly logged in", () => {
  assert.equal(looksLoggedOut([]), false);
  assert.equal(looksLoggedOut(undefined), false);
  assert.equal(looksLoggedOut(["Hi Sam", "My account", "Cart"]), false);
  assert.equal(looksLoggedOut(["Login help and FAQs"]), false);
});
