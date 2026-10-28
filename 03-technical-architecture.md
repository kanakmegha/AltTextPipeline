# Technical Architecture --- AI-Assisted Alt-Text Publishing Pipeline

**Status:** Proposed architecture\
**Version:** 0.1\
**Date:** 27 October 2026

## 1. Architecture goals

-   Process digitally generated PDFs incrementally.
-   Extract image/caption pairs and generate alt text asynchronously.
-   Let editors review while processing continues.
-   Keep binary assets out of the relational database.
-   Preserve prompt, draft, and approval history.
-   Recover from worker failures and restarts.
-   Keep the processing layer replaceable: develop/test in Colab, deploy
    as a normal Python worker.
-   Support a final DOCX and a transparency/audit ZIP.

## 2. Recommended logical components

1.  **Web frontend:** project creation, upload, prompt settings,
    progress, review/revision, approval, export.
2.  **Application API:** authentication, project/image records, state
    transitions, prompt configuration, signed downloads, export
    initiation.
3.  **Relational database:** project metadata, captions, filenames,
    statuses, alt-text versions, approvals, job records, exports.
4.  **File storage:** temporary per-project source PDF, extracted
    images, working artifacts, export files. This may be local disk on a
    persistent worker/server or object storage; do not store image bytes
    in database columns.
5.  **Job queue:** durable queue for page-chunk extraction, per-image
    generation, revision, archive creation, and DOCX export.
6.  **Worker processes:** Python modules using PyMuPDF, image utilities,
    AI-provider adapter, and `python-docx`.
7.  **AI provider:** vision-capable model called through a provider
    interface; credentials are server-side only.
8.  **Cleanup worker:** removes eligible temporary assets and orphan
    files based on policy.

## 3. Development versus production

### Development

Use Google Colab to test the existing extraction code, prompt, and DOCX
generation. Refactor notebook cells into importable Python
functions/modules and keep test fixtures small enough to run repeatedly.

### Production

Do not assume Colab is a durable general-purpose web backend. Deploy the
same Python modules as a background worker on a suitable VM, internal
office server, or managed worker platform. Select the deployment only
after confirming office firewall rules, AI provider policy, disk
requirements, and budget.

## 4. High-level flow

1.  API creates a project and stores the PDF in temporary file storage.
2.  API records the upload metadata and enqueues an extraction job.
3.  Extractor processes page ranges/chunks. It extracts embedded images
    and text-block geometry, creates stable image records, and stores
    image binaries in the project working directory/prefix.
4.  Caption matcher examines text blocks above and below the image,
    considering horizontal overlap, distance, columns, reading order,
    and page boundaries. Ambiguous matches are marked for editor
    attention.
5.  Once an image/caption pair is ready, enqueue a generation job
    immediately.
6.  Generation worker reads the image and caption, combines the approved
    base prompt, language instruction, and optional publisher
    instruction, calls the model, validates the result, and saves a new
    `AltTextVersion`.
7.  Frontend receives state updates via SSE/WebSocket/polling and
    displays ready images while other jobs continue.
8.  Editor revision requests enqueue a job for the selected image only.
9.  Approval transaction binds approval to an exact `AltTextVersion`.
10. When all required images are approved and blocking issues are
    resolved, the API enqueues DOCX export.
11. Export worker builds the document in reading order, validates it,
    saves it, and records checksum/status.
12. The editor downloads the final DOCX and audit ZIP. Cleanup removes
    temporary assets only when retention/delivery conditions are
    satisfied.

## 5. Processing strategy

### 5.1 Page-chunk extraction

-   Use PyMuPDF to inspect PDF pages, text blocks, bounding boxes, and
    embedded image objects.
-   Start with a configurable batch size such as 10--20 pages, then
    benchmark.
-   Preserve original page numbers and reading order.
-   Avoid rendering every page to a high-resolution bitmap by default.
-   Consider image reuse, inline images, vector figures, compound
    figures, clipping, and duplicate xrefs. Document what the first
    implementation supports.
-   A caption may be above or below the image; evaluate both. Consider
    neighbour-page context at chunk boundaries.
-   For digital PDFs, OCR is a fallback only when text extraction fails
    for particular content.

### 5.2 Image identity and filenames

-   Assign each logical image a UUID or stable internal ID independent
    of the filename.
-   Preserve the original caption separately from any editor-corrected
    caption.
-   Derive a proposed filename from the caption according to a
    deterministic sanitization policy.
-   Resolve collisions using a suffix or another documented
    deterministic rule.
-   Use content hashes to detect duplicate binaries when useful, but do
    not merge logical image records solely because binary content
    matches.
-   Never use untrusted filenames directly as filesystem paths.

### 5.3 Per-image asynchronous generation

-   Queue generation as soon as the image and caption are available.
-   Use bounded concurrency and provider-aware rate limiting.
-   Store job state durably.
-   Retry transient failures with exponential backoff and jitter.
-   Make handlers idempotent using job IDs and stable image/version
    identifiers.
-   Do not re-run successful generation for a whole book when one image
    fails.
-   Treat model output as a draft, never as an approval.

## 6. State model

### Image states

`extracted` → `queued` → `generating` → `ready_for_review` → `approved`

Revision path: `ready_for_review` or `approved` → `revision_queued` →
`revising` → `ready_for_review`

Other states: `needs_caption_review`, `failed`, `cancelled`.

If an approved image is revised, approval of the prior version remains
historical but is not approval of the new version. Export readiness
checks the currently selected version and its approval.

### Project readiness

Project state is derived from child jobs and image records. Example
aggregate indicators: - pages extracted / total pages - images
discovered - generation queued / running / failed - images ready for
review - images approved - export state

Do not use a single project status as the only record of progress.

## 7. Data model

### Project

-   `id`
-   `name`
-   `source_filename`
-   `source_checksum`
-   `language_variant` (`en-GB` or `en-US`)
-   `prompt_config_id` / version
-   `formatting_config` (JSON)
-   `status`
-   timestamps

### ImageAsset

-   `id`
-   `project_id`
-   `source_page`
-   `reading_order`
-   `bounding_box` (if available)
-   `original_caption`
-   `corrected_caption` (optional)
-   `filename`
-   `storage_key`
-   `checksum`
-   `caption_match_status`
-   `processing_status`

### AltTextVersion

-   `id`
-   `image_id`
-   `version_number`
-   `text`
-   `revision_instruction` (nullable)
-   `prompt_version_id`
-   `provider` / `model` (when available)
-   timestamps

### Approval

-   `id`
-   `image_id`
-   `alt_text_version_id`
-   reviewer ID (if applicable)
-   timestamp

### Job

-   `id`
-   `project_id`
-   `image_id` (nullable for page range/project jobs)
-   `job_type`
-   `range_start` / `range_end` (for extraction)
-   `status`
-   `attempt_count`
-   `error_code` / safe error detail
-   timestamps

### Export

-   `id`
-   `project_id`
-   `export_type` (`docx`, `audit_zip`)
-   `storage_key`
-   `status`
-   `checksum`
-   timestamps

### PromptVersion

-   `id`
-   `language_variant`
-   stable base prompt version
-   publisher instruction
-   assembled prompt snapshot or immutable reference
-   model configuration metadata
-   timestamp

## 8. Storage and cleanup

### Storage policy

-   Source PDF and extracted image binaries live outside PostgreSQL.
-   Use a per-project temporary directory/prefix,
    e.g. `work/{project_id}/`.
-   Keep only the active batch's intermediate files when possible.
-   Do not delete images still required for review or export.
-   Keep approved text, captions, prompt snapshots, and approval
    metadata in the database if the publisher needs project history.
-   The final DOCX and audit ZIP must remain available long enough for
    successful delivery; use durable storage or confirmed download/save
    behaviour.

### Cleanup policy

-   Mark assets eligible for cleanup only after successful export and
    required delivery/retention conditions.
-   Cleanup must be idempotent and must not delete files belonging to
    active jobs.
-   Maintain a cleanup status and last error.
-   Run periodic orphan-file reconciliation.
-   If a worker's disk is ephemeral, copy needed state/assets to durable
    storage before allowing the job to pause or resume.
-   Never treat "browser received a response" as proof that the final
    artifact has been safely retained.

## 9. API shape (illustrative)

-   `POST /api/projects` --- create project and upload or initialise
    upload.
-   `GET /api/projects/{project_id}` --- project summary and progress.
-   `GET /api/projects/{project_id}/images` --- paginated image
    inventory and status filters.
-   `GET /api/images/{image_id}` --- image/caption/current draft/version
    summary.
-   `POST /api/images/{image_id}/generate` --- queue or retry initial
    generation.
-   `POST /api/images/{image_id}/revisions` --- submit revision
    instruction.
-   `POST /api/images/{image_id}/approvals` --- approve exact draft
    version.
-   `PATCH /api/images/{image_id}` --- correct caption or filename with
    validation/audit.
-   `GET /api/projects/{project_id}/events` --- SSE progress events, if
    SSE is selected.
-   `POST /api/projects/{project_id}/exports/docx` --- request DOCX
    export.
-   `POST /api/projects/{project_id}/exports/audit-zip` --- request
    audit package.
-   `GET /api/exports/{export_id}` --- status/download URL.

Use authentication and authorization appropriate to deployment. These
endpoints are proposals, not a final API contract.

## 10. Prompt assembly and AI adapter

-   Stable base prompt: accessibility principles, factual grounding,
    required output shape.
-   Language instruction: British English (`en-GB`) or US English
    (`en-US`).
-   Publisher instructions: editable project-level additions.
-   Image caption and any configured surrounding context.
-   Revision instruction for revision jobs.

The exact user-supplied prompt will be reviewed separately and must not
be invented in this architecture. Store the assembled prompt snapshot
used for each generation. Keep API keys in server-side secrets.

## 11. Performance and observability

Record timings for: - upload - extraction per page chunk - caption
matching - time to first image ready for review - AI generation and
revision - export - cleanup

Track queue depth, retry count, provider rate limits, worker
memory/disk, temporary storage peak, and failures by stage. Establish
numeric SLOs after testing representative books.

## 12. Failure handling

-   **AI provider timeout:** retry transient errors with backoff;
    preserve prior drafts.
-   **Worker restart:** requeue unfinished jobs from durable state.
-   **Extraction error on one page:** record the page failure and
    continue where safe; allow retry.
-   **Ambiguous caption:** flag for editor correction rather than
    silently guessing.
-   **Duplicate filename:** deterministic suffix; preserve stable image
    ID.
-   **Export failure:** retain approved records and retry export only.
-   **Disk full:** stop accepting additional work, report the issue,
    clean eligible intermediates, and avoid partial deletion of active
    project assets.
-   **Cleanup failure:** retain a retryable cleanup record and alert/log
    safely.
-   **Prompt change:** affects new generations/revisions only; never
    silently alter prior approved text.

## 13. Security

-   Confirm whether book content may be sent to an external AI provider.
-   Do not expose AI credentials in frontend code, browser logs, or
    downloadable archives.
-   Validate uploaded PDFs and impose configurable size/page limits.
-   Sanitize filenames and prevent path traversal.
-   Restrict project and asset access.
-   Redact secrets and avoid logging full documents or sensitive content
    unnecessarily.
-   Define deletion and retention behaviour in advance.

## 14. Deployment decision gate

Before production deployment, confirm: 1. office firewall and hosting
permissions; 2. AI provider and book confidentiality requirements; 3.
expected largest PDF and extracted asset size; 4. available disk and
worker resources; 5. whether workers need to survive browser
closure/restart; 6. whether one or multiple editors use the system; 7.
retention and export-delivery requirements.

For an early prototype, use a single API + worker process and local
temporary disk on a persistent machine, with a relational database for
state. Introduce a separate durable queue/object storage when load or
reliability needs justify it. Do not use Google Colab as the only
production coordinator.
