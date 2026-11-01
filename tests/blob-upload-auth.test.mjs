import test from "node:test";
import assert from "node:assert/strict";
import esbuild from "esbuild";
import { buildClaimHeaders, claimUploadIntent, safeClaimDetail } from "../api/blob-upload.ts";

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
  assert.equal(detail, "detail: Bad credential Bearer [redacted] for [redacted]");
});

test("internal claim request forwards app credentials and safely explains Vercel protection 401", async () => {
  const originalFetch = globalThis.fetch;
  const headers = {
    cookie: "alttext_session=session-cookie-secret; alttext_csrf=csrf-cookie-secret",
    authorization: "Bearer test-bearer-secret",
    "x-operator-token": "operator-header-secret",
    "x-vercel-protection-bypass": "must-not-be-forwarded",
  };
  const vercelProtectionResponse = {
    message: "Protected by Vercel Authentication",
    error: { code: "401", message: "Protected deployment" },
    access: "vercel curl https://deployment.example/?nonce=private-nonce",
    actor: { email: "person@example.com" },
  };
  let captured;
  globalThis.fetch = async (url, options) => {
    captured = { url: String(url), options };
    return new Response(JSON.stringify(vercelProtectionResponse), {
      status: 401,
      headers: { "content-type": "application/json" },
    });
  };
  try {
    await assert.rejects(
      claimUploadIntent("deploy.example", "upload-key", "csrf-proof-secret", headers),
      (error) => {
        assert.equal(error.message.split("): ")[1].length <= 400, true);
        assert.match(error.message, /HTTP 401\): message: Protected by Vercel Authentication/);
        assert.match(error.message, /error\.code: 401/);
        assert.match(error.message, /error\.message: Protected deployment/);
        for (const secret of [
          "session-cookie-secret", "csrf-cookie-secret", "test-bearer-secret",
          "operator-header-secret", "csrf-proof-secret", "person@example.com", "private-nonce",
        ]) assert.equal(error.message.includes(secret), false);
        return true;
      },
    );
    assert.equal(captured.url, "https://deploy.example/api/uploads/upload-key/claim-token");
    assert.equal(captured.options.method, "POST");
    assert.equal(captured.options.headers.Cookie, headers.cookie);
    assert.equal(captured.options.headers["X-CSRF-Token"], "csrf-proof-secret");
    assert.equal(captured.options.headers.Authorization, headers.authorization);
    assert.equal(captured.options.headers["X-Operator-Token"], headers["x-operator-token"]);
    assert.equal("x-vercel-protection-bypass" in captured.options.headers, false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("claim diagnostics never echo unstructured upstream content", () => {
  const detail = safeClaimDetail("private-cookie-value\n" + "x".repeat(500), {}, "csrf");
  assert.equal(detail, "Non-JSON upstream response");
  assert.equal(detail.includes("private-cookie-value"), false);
});

test("claim diagnostics cap structured details at 400 characters", () => {
  const detail = safeClaimDetail(JSON.stringify({ message: "x".repeat(500) }), {}, "csrf");
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
