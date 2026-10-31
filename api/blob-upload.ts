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
  let detail = responseText;
  try {
    const parsed = JSON.parse(responseText) as { detail?: unknown; error?: unknown };
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
        const claim = await fetch(`https://${host}/api/uploads/${encodeURIComponent(clientPayload.upload_key)}/claim-token`, {
          method: "POST",
          headers: buildClaimHeaders(req.headers, clientPayload.csrf_token),
          body: "{}",
          cache: "no-store",
        });
        if (!claim.ok) {
          const safeDetail = safeClaimDetail(await claim.text(), req.headers, clientPayload.csrf_token);
          throw new Error(`Upload intent claim failed (HTTP ${claim.status}): ${safeDetail}`);
        }
        const intent = await claim.json() as { pathname: string; max_size_bytes: number; valid_until: number };
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
