import { handleUpload } from "@vercel/blob/client";

type ClientPayload = { upload_key?: string; csrf_token?: string };

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
          headers: {
            "Content-Type": "application/json",
            "Cookie": req.headers.cookie || "",
            "X-CSRF-Token": clientPayload.csrf_token,
          },
          body: "{}",
          cache: "no-store",
        });
        if (!claim.ok) throw new Error("Upload intent could not be claimed");
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
