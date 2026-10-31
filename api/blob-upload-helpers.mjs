/** Build the narrowly scoped credential set accepted by FastAPI auth middleware. */
export function buildClaimHeaders(incomingHeaders, csrfToken) {
  const headers = {
    "Content-Type": "application/json",
    Cookie: typeof incomingHeaders.cookie === "string" ? incomingHeaders.cookie : "",
    "X-CSRF-Token": csrfToken,
  };
  if (typeof incomingHeaders.authorization === "string" && incomingHeaders.authorization) {
    headers.Authorization = incomingHeaders.authorization;
  }
  if (typeof incomingHeaders["x-operator-token"] === "string" && incomingHeaders["x-operator-token"]) {
    headers["X-Operator-Token"] = incomingHeaders["x-operator-token"];
  }
  return headers;
}

/** Keep upstream claim diagnostics useful while removing credentials and controls. */
export function safeClaimDetail(responseText, incomingHeaders, csrfToken) {
  let detail = responseText;
  try {
    const parsed = JSON.parse(responseText);
    detail = String(parsed.detail ?? parsed.error ?? responseText);
  } catch { /* Use bounded plain text when the upstream response is not JSON. */ }
  detail = detail.replace(/[\x00-\x1f\x7f]/g, " ");
  for (const secret of [
    incomingHeaders.authorization,
    incomingHeaders["x-operator-token"],
    csrfToken,
    incomingHeaders.cookie,
  ]) {
    if (typeof secret === "string" && secret.length >= 8) {
      detail = detail.split(secret).join("[redacted]");
    }
  }
  return detail.slice(0, 400) || "No response detail";
}
