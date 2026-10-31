import test from "node:test";
import assert from "node:assert/strict";
import esbuild from "esbuild";
import { buildClaimHeaders, safeClaimDetail } from "../api/blob-upload.ts";

test("claim request forwards supported auth credentials and CSRF only", () => {
  const headers = buildClaimHeaders({
    cookie: "alttext_session=session-value; alttext_csrf=csrf-value",
    authorization: "Bearer operator-bearer",
    "x-operator-token": "operator-header-token",
    "x-unrelated-secret": "must-not-forward",
    host: "incoming.example",
  }, "csrf-value");
  assert.deepEqual(headers, {
    "Content-Type": "application/json",
    Cookie: "alttext_session=session-value; alttext_csrf=csrf-value",
    "X-CSRF-Token": "csrf-value",
    Authorization: "Bearer operator-bearer",
    "X-Operator-Token": "operator-header-token",
  });
  assert.equal(JSON.stringify(headers).includes("must-not-forward"), false);
});

test("claim diagnostics preserve status detail while redacting credentials", () => {
  const detail = safeClaimDetail(JSON.stringify({
    detail: "Bad credential Bearer operator-bearer for operator-header-token",
  }), {
    authorization: "Bearer operator-bearer",
    "x-operator-token": "operator-header-token",
    cookie: "cookie-secret-value",
  }, "csrf-secret-value");
  assert.equal(detail, "Bad credential [redacted] for [redacted]");
});

test("claim diagnostics cap plain-text details and remove control characters", () => {
  const detail = safeClaimDetail("failure\n" + "x".repeat(500), {}, "csrf");
  assert.equal(detail.length, 400);
  assert.equal(detail.includes("\n"), false);
});

test("CommonJS handler bundle has no runtime require of an ES module helper", async () => {
  const result = await esbuild.build({
    entryPoints: ["api/blob-upload.ts"],
    bundle: true,
    platform: "node",
    format: "cjs",
    external: ["@vercel/blob/client"],
    write: false,
  });
  const bundle = result.outputFiles[0].text;
  assert.doesNotMatch(bundle, /require\([^)]*\.mjs/);
  assert.match(bundle, /Upload intent claim failed/);
});
