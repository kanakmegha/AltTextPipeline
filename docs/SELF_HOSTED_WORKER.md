# Self-hosted extraction worker

The worker can run on a MacBook, Linux host, or VM while the web application remains on Vercel. It opens outbound PostgreSQL and private Vercel Blob connections only; it exposes no HTTP port. PostgreSQL is the durable job queue. Redis Streams is optional and acts as an additional wake-up path when configured.

## Flow and recovery

Vercel creates a queued job after verifying the private Blob upload. The worker polls PostgreSQL, atomically claims a job with a worker ID and lease, downloads the source once to a temporary file, and processes bounded page batches in separate processes. Each batch persists its images and page metadata before it is marked complete. The final grouping step combines batch context to preserve the existing cross-page caption association behavior.

On interruption, the lease expires and another worker may claim the job. Image identity is unique by project, page, xref, and placement, so replay does not create duplicate image records. Completed batches are re-extracted after a process restart to rebuild the in-memory cross-page grouping context; their persisted image rows are reused. This favors correctness and bounded memory over skipping all already-completed CPU work.

SIGTERM and SIGINT stop the polling loop. In-flight job work is allowed to finish within the container grace period; if the process is terminated first, PostgreSQL lease recovery makes the job claimable later. Temporary PDFs are removed in a `finally` block and no job state is stored permanently in the container.

## Requirements and setup

- Docker Desktop on macOS, or Docker Engine plus Compose on Linux.
- Network access from the host to the same Neon/PostgreSQL database and private Vercel Blob store used by the Vercel application.
- A Vercel Blob read/write token kept only in the worker environment.

Copy `.env.worker.example` to `.env.worker` and set `DATABASE_URL`, `BLOB_READ_WRITE_TOKEN`, and `BLOB_STORE_ID`. Do not commit `.env.worker`; it is excluded from Git and Docker build context. The database must have application migrations applied before the worker starts. Use a database role with only the schema permissions the application requires, and restrict its network access to trusted worker/API egress where practical.

## Run with Docker

```sh
cp .env.worker.example .env.worker
# Edit .env.worker with the database and Blob credentials.
docker compose build worker
docker compose up -d --build worker
docker compose logs -f worker
docker compose stop worker
docker compose restart worker
```

The one-command workflow is `docker compose up -d --build worker`. Compose restarts a stopped worker automatically. Logs go to stdout/stderr and are rotated by Docker. To check activity, inspect logs and confirm jobs move from `queued` to `running` and then `completed`, with their project becoming `ready_for_review` and batch progress increasing.

Run without Docker using the same environment values:

```sh
python -m src.worker
```

When `REDIS_URL` is empty, the process polls PostgreSQL directly every `WORKER_POLL_SECONDS` seconds. When Redis is configured, it retains Redis Streams consumption and still polls PostgreSQL for missed publications and stale-job recovery.

## Configuration and resource tuning

| Variable | Default | Purpose |
|---|---:|---|
| `DATABASE_URL` | Required | Shared PostgreSQL database. |
| `BLOB_READ_WRITE_TOKEN` | Required | Private Blob access for source downloads and image uploads. |
| `BLOB_STORE_ID` | Optional | Selects the same Blob store as the API. |
| `WORKER_POLL_SECONDS` | `5` | Delay between database polling cycles. |
| `PDF_BATCH_SIZE` | `5` | Pages in each independent extraction batch. |
| `MAX_PARALLEL_BATCHES` | `2` | Maximum concurrently active child processes. |
| `MAX_RETRIES` | `3` | Maximum job and batch attempts. |
| `WORKER_MAX_MEMORY_MB` | `2048` | Compose container memory cap and temporary filesystem size. |
| `WORKER_MAX_CPU` | `2` | Compose CPU quota. |
| `REDIS_URL` | Empty | Optional Redis Streams transport. |

Compose reads `WORKER_MAX_MEMORY_MB` and `WORKER_MAX_CPU` for resource limits from the invoking shell (with defaults shown above); set them before running Compose to tune the limits, for example `WORKER_MAX_MEMORY_MB=4096 WORKER_MAX_CPU=3 docker compose up -d --build worker`. Set the matching values in `.env.worker` as well for application-level configuration. `MAX_PARALLEL_BATCHES` directly controls the process pool; keep it conservative on laptops and reduce `PDF_BATCH_SIZE` for large image-heavy documents. Docker Desktop must have enough memory allocated for both the host and the container. The container is read-only except for a bounded `/tmp` tmpfs, drops Linux capabilities, runs as a non-root user, and has no published ports.

No higher concurrency setting is recommended without benchmarking on the target host and representative PDFs. Docker constrains the resources available; it does not add compute capacity.

### Local benchmark snapshot

On 2026-10-31, a single local run processed a generated 40-page PDF with one 256×256 PNG and a short text caption per page, using batch size 5 and two process workers. It completed 8 batches and persisted 40 images in 1.51 seconds; child-process CPU time was 1.33 seconds. The Python parent peaked at 51.5 MB RSS and the largest child at 51.7 MB RSS. This was measured with local SQLite and filesystem storage on macOS/Python 3.11, without network Blob or Neon latency. It is a synthetic smoke benchmark, not a production capacity estimate or a concurrency comparison; measure representative PDFs on the target host before increasing parallelism.

## Move from a laptop to Linux or a VM

Use the same repository revision, Docker image, environment variables, and Compose commands on Linux or a future VM. No extraction code or machine-specific paths need to change. Stop the old worker before starting the replacement if you want to avoid overlapping capacity; database claims and leases still coordinate multiple instances safely.

## Troubleshooting and limitations

- `DATABASE_URL` failures: check Neon credentials, TLS settings, allow lists, and outbound connectivity.
- Blob failures: verify the token and store ID match the API's private store.
- Jobs remain queued: inspect worker logs, confirm the worker is polling the same database as Vercel, and confirm migrations have run.
- Repeated failures: inspect job and batch error messages, then verify the source PDF can be opened by PyMuPDF and the container has sufficient memory/disk-backed tmpfs space.
- When the laptop sleeps or loses connectivity, processing pauses. The API can continue creating durable queued jobs; processing resumes when the worker reconnects. A worker must remain online for extraction to progress.
- Batch grouping context is held in memory until the final cross-page association pass. Image payloads are persisted and released as each batch completes, but very large documents can still require substantial memory for text and grouping metadata. Tune batch size and parallelism and validate with representative files.
- Compose resource controls vary by Docker runtime; verify the limits in Docker Desktop or the Linux host before relying on them for strict isolation.
