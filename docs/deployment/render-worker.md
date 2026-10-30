# Render extraction worker and Vercel deployment

## Architecture

The browser uses the official `@vercel/blob/client` `upload()` flow. Its PDF requests go to Vercel Blob, while FastAPI receives only upload metadata, CSRF protected finalization calls, and small client upload token requests. The Blob store must be created as **private**. FastAPI verifies the private object’s server returned pathname, exact size, and content type using Blob metadata (HEAD) without downloading PDF bytes, then atomically creates the project and extraction job. The Render worker validates the PDF signature after downloading it from Blob and computes the source SHA-256 checksum there.

Vercel Queues’ Python SDK supports publishing and external poll mode. For a Render worker, this repository uses Redis Streams instead: Vercel Queues API requests require a short lived Vercel OIDC identity, and the Render process has no automatic Vercel runtime identity or documented refresh exchange. A copied OIDC token would expire. Redis Streams has a stable Render connection and consumer groups with pending-message recovery. PostgreSQL remains the durable source of truth: Render also scans queued and abandoned jobs, so failed Redis publishing does not lose work.

Local SQLite runs the existing synchronous extraction flow and does not import/connect to Redis or require Render, Vercel Queues, or a Redis service.

## Render background worker

Create a **Background Worker** service from the feature branch/repository. Use Python 3.11 and set:

- Build command: `pip install -r requirements.txt`
- Start command: `python -m src.worker`

Configure these environment variables in Render:

| Variable | Required | Value |
| --- | --- | --- |
| `DATABASE_URL` | Yes | Neon/PostgreSQL connection string for the same database used by Vercel. Use a connection mode appropriate for a continuously running worker. |
| `BLOB_READ_WRITE_TOKEN` | Yes | Server only. Private Blob read/write token for the store containing source PDFs and extracted images. |
| `BLOB_STORE_ID` | Optional | Store identifier if the application configuration requires it. |
| `REDIS_URL` | Yes | TLS Redis URL for a persistent Redis service reachable by both Vercel and Render. |
| `EXTRACTION_QUEUE_STREAM` | Optional | Defaults to `alttext:extraction`. |
| `EXTRACTION_QUEUE_GROUP` | Optional | Defaults to `render-workers`. Keep the same group for competing worker instances. |
| `WORKER_POLL_SECONDS` | Optional | Defaults to `2`. |
| `RENDER_INSTANCE_ID` | Optional | Stable unique name per Render instance; defaults to a process-specific name. |

Use a Redis service with persistence and a retention policy suitable for queue data. Permit network access from both Vercel Functions and the Render service. Redis messages contain only job IDs, never PDF contents. The worker acknowledges a stream message only after the job handler has recorded its result; unacknowledged entries are reclaimed after their idle lease. Database claims prevent duplicate extraction when messages are delivered more than once.

## Vercel application

1. Connect the repository and use the `codex/direct-blob-worker` feature branch for a Preview deployment. Do not change the production branch while reviewing this work. Vercel returns `410` at the routing layer for legacy `POST /api/upload-and-extract`; the existing SQLite local endpoint remains available.
2. Create or select a **private** Vercel Blob store. A store’s access mode cannot be changed after creation.
3. Set Vercel environment variables:
   - `DATABASE_URL`
   - `BLOB_READ_WRITE_TOKEN`
   - `BLOB_STORE_ID` if used
   - `REDIS_URL`
   - `MAX_UPLOAD_MB` (defaults to 50)
   - existing authentication and CSRF configuration, including `SESSION_SECRET_KEY`
   - existing AI provider variables, if AI generation is enabled
4. The `api/blob-upload.ts` Node function is explicitly routed before the Python catch-all in `vercel.json`. It calls Vercel’s `handleUpload()` and obtains a one-use, constrained client token. The Blob read/write token is never returned to the browser.
5. Before deploying frontend edits, run `npm ci && npm run build:frontend` and commit the generated `src/web/static/upload.bundle.js`. The lockfile pins the SDK/build tool dependencies; Vercel’s Node function build uses `package-lock.json`.
6. Deploy Preview and verify in browser developer tools that the PDF upload requests target Vercel Blob domains. The only same-origin requests should be metadata authorization, the small client-token request, and finalization. Then verify the source object is private and that the worker moves the project from `queued` to `ready_for_review`.
7. Configure the same database, Blob store, and Redis service on Render. Apply the application migrations before enabling the worker.

## Operational recovery

The worker scans the jobs table for queued jobs and stale `running` leases in addition to consuming Redis. If FastAPI cannot publish to Redis, the row remains queued and is found by that scan. A failed job is retried up to its configured attempt limit. Extraction inserts are guarded by stable group IDs and source image identity, so a retry after partial persistence does not create duplicate image records.

Large-file behavior has not been verified against a live Vercel project or Blob store in this repository environment. Complete the Preview verification above before treating the 4.5 MB function body limit as resolved in deployment.
