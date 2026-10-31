# Render extraction worker and Vercel deployment

## Architecture

The browser uses the official `@vercel/blob/client` `upload()` flow. Its PDF requests go to Vercel Blob, while FastAPI receives only upload metadata, CSRF protected finalization calls, and small client upload token requests. The Blob store must be created as **private**. FastAPI verifies the private object’s server returned pathname, exact size, and content type using Blob metadata (HEAD) without downloading PDF bytes, then atomically creates the project and extraction job. The Render worker validates the PDF signature after downloading it from Blob and computes the source SHA-256 checksum there.

PostgreSQL is the durable job queue and worker coordination layer. With `REDIS_URL` unset, Render polls PostgreSQL for queued and abandoned jobs; atomic claims and leases prevent concurrent processing across worker instances. Redis Streams remains an optional wake-up and message transport when `REDIS_URL` is configured. Database polling continues in that mode to recover missed publishes and abandoned jobs.

Local SQLite runs the existing synchronous extraction flow and does not import/connect to Redis or require Render, Vercel Queues, or a Redis service.

## Render background worker

Create a **Background Worker** service from the feature branch/repository. Use Python 3.11 and set:

- Build command: `pip install -r requirements.txt`
- Start command: `python -m src.worker`

Configure these environment variables in Render:

| Variable | Required | Value |
| --- | --- | --- |
| `DATABASE_URL` | Yes | Neon/PostgreSQL connection string for the same database used by Vercel. Use a connection mode appropriate for a continuously running worker. |
| `BLOB_READ_WRITE_TOKEN` | Yes | Server only. Use the same private Blob read/write token configured on the FastAPI deployment. |
| `BLOB_STORE_ID` | Optional | Use the same store identifier as the FastAPI deployment when configured. |
| `REDIS_URL` | No | Optional TLS Redis URL. When absent, the worker polls PostgreSQL directly. |
| `EXTRACTION_QUEUE_STREAM` | Optional | Defaults to `alttext:extraction`. |
| `EXTRACTION_QUEUE_GROUP` | Optional | Defaults to `render-workers`. Keep the same group for competing worker instances. |
| `WORKER_POLL_SECONDS` | Optional | Defaults to `5` seconds between polling iterations. |
| `RENDER_INSTANCE_ID` | Optional | Stable unique name per Render instance; defaults to a process-specific name. |

If Redis is configured, use a service with persistence and a retention policy suitable for queue data. Permit network access from both Vercel Functions and Render. Redis messages contain only job IDs, never PDF contents. The worker acknowledges a stream message only after the handler returns; unacknowledged entries are reclaimed after their idle lease. PostgreSQL claims prevent duplicate extraction when messages are delivered more than once. No Redis service or `REDIS_URL` is needed for PostgreSQL polling mode.

## Vercel application

1. Connect the repository and use the `codex/direct-blob-worker` feature branch for a Preview deployment. Do not change the production branch while reviewing this work. Vercel returns `410` at the routing layer for legacy `POST /api/upload-and-extract`; the existing SQLite local endpoint remains available.
2. Create or select a **private** Vercel Blob store. A store’s access mode cannot be changed after creation.
3. Set Vercel environment variables:
   - `DATABASE_URL`
   - `BLOB_READ_WRITE_TOKEN`
   - `BLOB_STORE_ID` if used
   - Vercel automatically selects Blob storage; set `STORAGE_BACKEND=blob` for non-Vercel FastAPI deployments that should use the same store.
   - `REDIS_URL` (optional; omit it for PostgreSQL polling mode)
   - `MAX_UPLOAD_MB` (defaults to 50)
   - existing authentication and CSRF configuration, including `SESSION_SECRET_KEY`
   - existing AI provider variables, if AI generation is enabled
4. The `api/blob-upload.ts` Node function is explicitly routed before the Python catch-all in `vercel.json`. It calls Vercel’s `handleUpload()` and obtains a one-use, constrained client token. The Blob read/write token is never returned to the browser.
5. Before deploying frontend edits, run `npm ci && npm run build:frontend` and commit the generated `src/web/static/upload.bundle.js`. The lockfile pins the SDK/build tool dependencies; Vercel’s Node function build uses `package-lock.json`.
6. Deploy Preview and verify in browser developer tools that the PDF upload requests target Vercel Blob domains. The only same-origin requests should be metadata authorization, the small client-token request, and finalization. Then verify the source object is private and that the worker moves the project from `queued` to `ready_for_review`.
7. Configure the same database and Blob store on Render. Configure Redis only if using Streams transport. Apply the application migrations before enabling the worker.

## Operational recovery

The worker scans the jobs table for queued jobs and stale `running` leases on every polling iteration, whether or not Redis is configured. A queued job created by FastAPI is therefore discoverable without Redis. A failed job is retried up to its configured attempt limit. Extraction inserts are guarded by stable group IDs and source image identity, so a retry after partial persistence does not create duplicate image records.

Large-file behavior has not been verified against a live Vercel project or Blob store in this repository environment. Complete the Preview verification above before treating the 4.5 MB function body limit as resolved in deployment.
