import { handleUpload } from "@vercel/blob/client";

type ClientPayload = { upload_key?: string; csrf_token?: string };

/** Build the narrowly scoped credential set accepted by FastAPI auth middleware. */
export function buildClaimHeaders(incomingHeaders: Record<string, unknown>, csrfToken: string) {
  const headers: Record<string, string> = {
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
export function safeClaimDetail(
  responseText: string,
  incomingHeaders: Record<string, unknown>,
  csrfToken: string,
): string {
  let values: string[] = [];
  const collect = (value: unknown, keyPath: string, depth = 0) => {
    if (depth > 3) return;
    if (typeof value === "string") {
      values.push(`${keyPath}: ${value}`);
      return;
    }
    if (!value || typeof value !== "object" || Array.isArray(value)) return;
    const fields = value as Record<string, unknown>;
    for (const key of ["message", "detail", "error", "code"]) {
      if (fields[key] !== undefined) collect(fields[key], keyPath ? `${keyPath}.${key}` : key, depth + 1);
    }
  };

  let parsedJson = false;
  try {
    parsedJson = true;
    collect(JSON.parse(responseText), "");
  } catch { parsedJson = false; /* Use bounded plain text when the upstream response is not JSON. */ }

  let detail = values.length
    ? values.join(" | ")
    : parsedJson ? "No safe detail in response" : "Non-JSON upstream response";
  detail = detail
    .replace(/[\x00-\x1f\x7f]/g, " ")
    .replace(/\bBearer\s+[^\s,;]+/gi, "Bearer [redacted]")
    .replace(/\b(token|secret|password|cookie|authorization|session|nonce)\b\s*[:=]\s*[^\s,;]+/gi, "$1=[redacted]")
    .replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, "[redacted-email]")
    .replace(/https?:\/\/[^\s"'<>]+/gi, "[redacted-url]")
    .replace(/\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b/gi, "[redacted-id]")
    .replace(/\b(?:\d{1,3}\.){3}\d{1,3}\b/g, "[redacted-ip]");

  for (const secret of [
    incomingHeaders.authorization,
    incomingHeaders["x-operator-token"],
    csrfToken,
    incomingHeaders.cookie,
  ]) {
    if (typeof secret === "string" && secret.length > 0) {
      detail = detail.split(secret).join("[redacted]");
    }
  }

  if (typeof incomingHeaders.cookie === "string") {
    for (const pair of incomingHeaders.cookie.split(";")) {
      const separator = pair.indexOf("=");
      const value = separator >= 0 ? pair.slice(separator + 1).trim() : "";
      if (value) detail = detail.split(value).join("[redacted]");
    }
  }
  return detail.slice(0, 400) || "No safe detail in response";
}

export async function claimUploadIntent(
  host: string,
  uploadKey: string,
  csrfToken: string,
  incomingHeaders: Record<string, unknown>,
): Promise<{ pathname: string; max_size_bytes: number; valid_until: number }> {
  const response = await fetch(`https://${host}/api/uploads/${encodeURIComponent(uploadKey)}/claim-token`, {
    method: "POST",
    headers: buildClaimHeaders(incomingHeaders, csrfToken),
    body: "{}",
    cache: "no-store",
  });
  if (!response.ok) {
    const detail = safeClaimDetail(await response.text(), incomingHeaders, csrfToken);
    throw new Error(`Upload intent claim failed (HTTP ${response.status}): ${detail}`);
  }
  return await response.json() as { pathname: string; max_size_bytes: number; valid_until: number };
}

export default async function handler(req: any, res: any) {
  if (req.method !== "POST") return res.status(405).json({ error: "Method not allowed" });
  let clientPayload: ClientPayload = {};
  try {
    const result = await handleUpload({
      request: req,
      body: req.body,
      onBeforeGenerateToken: async (pathname, rawPayload) => {
        clientPayload = JSON.parse(rawPayload || "{}") as ClientPayload;
        if (!clientPayload.upload_key || !clientPayload.csrf_token) {
          throw new Error("Missing upload intent or CSRF proof");
        }
        const host = process.env.VERCEL_URL;
        if (!host) throw new Error("VERCEL_URL is unavailable");
        const intent = await claimUploadIntent(host, clientPayload.upload_key, clientPayload.csrf_token, req.headers);
        if (pathname !== intent.pathname) throw new Error("Blob path does not match the upload intent");
        return {
          allowedContentTypes: ["application/pdf"],
          maximumSizeInBytes: intent.max_size_bytes,
          validUntil: intent.valid_until,
          allowOverwrite: false,
          addRandomSuffix: false,
        };
      },
      onUploadCompleted: async () => {
        // FastAPI performs authoritative verification and finalization on /complete.
      },
    });
    return res.status(200).json(result);
  } catch (error) {
    console.error("Blob client upload token request failed", error);
    return res.status(400).json({ error: "Upload token request rejected" });
  }
}
