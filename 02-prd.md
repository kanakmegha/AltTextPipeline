# Product Requirements Document (PRD) --- AI-Assisted Alt-Text Publishing Pipeline

**Status:** Draft\
**Version:** 0.1\
**Date:** 27 October 2026

## 1. Product objective

Provide a web-based editorial workflow for extracting images and
captions from digitally generated book PDFs, generating image alt text,
reviewing and revising each draft, and exporting approved results to a
configurable Word document.

The application must support asynchronous processing and review,
transparent prompt configuration, recoverable jobs, and temporary asset
storage.

## 2. Assumptions

-   Source documents are digitally generated PDFs, not scanned books.
-   Existing Python/Colab code already extracts images with captions
    below them; support for captions above images must be tested and
    improved.
-   The user has an existing alt-text prompt that will be reviewed and
    supplied separately.
-   Every extracted image must receive alt text; decorative-image
    exclusion is not part of the current requirement.
-   British English and US English are project-level options.
-   The editor needs a downloadable folder/archive containing images and
    their caption data.
-   Word document formatting is configurable in project settings.
-   Processing and review should overlap.
-   Google Colab is a development/test environment. Production
    deployment must not depend on an unapproved or unreliable Colab
    runtime.

## 3. Roles

### Editor

Can create projects, upload PDFs, select language, view/edit permitted
prompt settings, monitor processing, review drafts, request revisions,
approve drafts, download audit assets, and export the final DOCX.

### Operator/admin (minimal MVP, if required)

Can configure AI credentials, worker settings, storage and retention
policies, and troubleshoot failed jobs. Admin UI may be deferred;
configuration can initially be environment-based.

## 4. Functional requirements

### FR-1: Project creation and upload

-   The editor can create a project and upload one PDF.
-   The UI displays upload progress and basic file details.
-   Validate file type, size limits, and PDF readability.
-   Create a unique project ID.
-   The application must not store PDF or image binaries in relational
    database fields.
-   A project can be cancelled; cleanup must not remove files while an
    active job still uses them.

**Acceptance criteria** - A valid PDF creates one project. - Invalid or
unreadable files produce an actionable error. - Re-uploading/retrying
does not silently create duplicate jobs for the same upload action.

### FR-2: Language and prompt configuration

-   The editor selects British English or US English.
-   The system combines a stable base prompt with language instructions
    and optional publisher instructions.
-   The editor can preview the exact assembled prompt before processing.
-   The exact prompt text/version and relevant generation configuration
    are recorded with each generated draft.
-   Prompt configuration changes apply to new generations/revisions
    only; they do not silently rewrite existing drafts.
-   Mandatory system/accessibility rules cannot be removed by the
    editable publisher-instruction field.

**Acceptance criteria** - Switching language updates the preview. - The
preview shows the complete prompt sent to the model, subject to provider
API constraints. - Each draft can be traced to its prompt version.

### FR-3: PDF image extraction

-   Process digital PDFs by page ranges/chunks rather than rasterising
    every page by default.
-   Extract embedded images and inspect text blocks and coordinates.
-   Support captions above and below images.
-   Preserve source page number, reading order, bounding-box/position
    metadata when available, original caption text, and extraction
    diagnostics.
-   Detect missing or ambiguous caption matches and mark them for
    correction.
-   Maintain a stable internal image ID independent of its filename.
-   Sanitize filenames and resolve duplicate names deterministically.
-   Avoid duplicate logical image records on retries.
-   Handle image reuse, multiple images per page, multi-column layouts,
    and vector/compound figures to the extent supported; document
    limitations and flag uncertain cases.

**Acceptance criteria** - A test suite covers captions above/below,
multiple images per page, multi-line captions, multi-column layouts,
no-caption cases, duplicate caption names, and page boundaries. -
Extraction can resume without duplicating completed records. - The
editor can correct a caption or filename before/after generation, with
changes recorded.

### FR-4: Incremental processing and job queue

-   Extraction produces records incrementally.
-   Each ready image/caption pair can be queued for generation without
    waiting for all pages to finish.
-   Generation jobs run with bounded concurrency and provider-aware rate
    limits.
-   Job status is tracked at project and image levels.
-   Failed jobs can be retried independently.
-   Retries are idempotent.
-   The editor can review completed items while other items are queued
    or processing.

**Suggested image states** `extracted`, `needs_caption_review`,
`queued`, `generating`, `ready_for_review`, `revision_queued`,
`revising`, `approved`, `failed`, `cancelled`.

Project status should be derived from its image/job states, not used as
the only source of truth.

**Acceptance criteria** - The first completed image can appear for
review before the whole book finishes. - A failed image can be retried
without reprocessing approved images. - A worker restart does not lose
persisted job state.

### FR-5: AI alt-text generation

-   Generate alt text from the image and its caption, following the
    approved prompt.
-   Use additional book context only if configured and needed.
-   Keep model output separate from approval state.
-   Validate response format and handle empty, malformed, or failed
    responses.
-   Record model/provider, prompt version, generation timestamp, and
    token/cost metadata when available.
-   Avoid logging secrets or unnecessary book content.

**Acceptance criteria** - A successful response creates a draft
version. - A failed response creates an actionable error and retry
path. - The system never automatically approves model output.

### FR-6: Review and revision

-   The review screen displays the image, filename, original caption,
    current alt text, status, and source page.
-   The editor can approve the current draft or enter a revision
    instruction.
-   Revisions are generated for only the selected image.
-   Show the new result promptly when generation completes.
-   Preserve earlier drafts and instructions.
-   Approval applies to a specific draft version.
-   Any new revision or material change invalidates the prior approval
    until the new version is approved.
-   Support previous/next navigation and filters for status.
-   Autosave editor actions where appropriate.

**Acceptance criteria** - The editor can approve an image while other
images are processing. - A revised draft cannot remain marked approved
based on an older version. - The editor can inspect previous versions
and the instruction used.

### FR-7: Transparency/audit package

-   The editor can download an archive of extracted images and
    associated caption files as extraction proceeds or when available.
-   Each file's basename should follow the sanitized caption-derived
    filename policy, without the image extension where required for the
    text record.
-   Include a manifest mapping stable image ID, filename, source page,
    caption, processing status, and relevant prompt
    configuration/version.
-   Do not include secrets, API keys, or hidden credentials.
-   Make clear whether an archive is a partial snapshot or final
    package.

**Suggested package layout**

``` text
book-project-audit.zip
  manifest.json
  prompt/
    assembled-prompt.txt
    prompt-config.json
  images/
    Figure_001.png
    Figure_002.jpg
  captions/
    Figure_001.txt
    Figure_002.txt
```

The exact filename rules and caption-file format must be agreed before
implementation.

### FR-8: Word export

-   Export is enabled only when every required image has an approved alt
    text and there are no blocking failures or unresolved caption
    issues.
-   Use the project's formatting settings.
-   Preserve original reading order.
-   For each image, include the filename without extension, the image,
    and the approved alt text in the required format: `Alt Text:`
    followed by the approved text.
-   Add captions, source-page references, headings, spacing, image
    sizing, and page breaks according to configuration.
-   Verify the DOCX can be reopened and expected content is present
    before marking export successful.
-   Retain the final DOCX until delivered or saved to a durable
    destination.
-   Re-export should use persisted approved records, not re-run AI.

**Acceptance criteria** - No unapproved alt text is included as approved
output. - All expected images appear exactly once unless the chosen
template explicitly specifies otherwise. - Output order matches the
selected order. - A failed export can be retried without repeating AI
generation.

### FR-9: Temporary storage and cleanup

-   Store binaries in temporary disk/object storage, not the relational
    database.
-   Use a per-project working directory/prefix.
-   Track file ownership, project ID, and lifecycle.
-   Delete intermediates only after successful export and delivery
    requirements are met.
-   Support configurable retention, cleanup on cancellation, and a
    periodic orphan-file cleanup job.
-   If storage is ephemeral, ensure the source or intermediate assets
    can be restored before a job resumes.
-   Never delete the only copy of an image that the editor still needs
    to review.

### FR-10: Progress and user feedback

-   Show progress for extraction, queued/generating images,
    ready-for-review images, approved images, failed images, and export.
-   Updates should be live or near-live using SSE, WebSockets, or
    polling.
-   Provide clear retry and cancellation actions where safe.
-   Distinguish "processing complete" from "editor review complete."

## 5. Non-functional requirements

### Performance

-   Measure upload time, extraction time, time to first reviewable
    image, AI latency, revision latency, and export time.
-   Use bounded concurrency; avoid unbounded model calls.
-   Do not rasterise every page unless required.
-   Use image resizing for AI only when quality remains adequate; retain
    original extracted assets for output.
-   Set numeric service targets after benchmarking representative books.

### Reliability

-   Durable job state, idempotent handlers, retry with exponential
    backoff, and graceful recovery.
-   A job failure must not erase approved work.
-   Use checksums or stable identifiers where helpful to detect
    duplicate files.

### Security and privacy

-   Confirm whether book content may be sent to an external AI provider.
-   Use authentication if deployed beyond a trusted single-user
    environment.
-   Restrict file access by project.
-   Validate file uploads and sanitize paths/filenames.
-   Keep credentials in environment secrets, never in frontend code or
    audit ZIPs.
-   Apply a defined retention/deletion policy.

### Accessibility and usability

-   Review controls must be keyboard accessible.
-   Status must be communicated in text, not color alone.
-   Ensure image preview and alt-text editing remain usable at typical
    office desktop resolutions.

## 6. Data model (logical)

-   `Project`: id, name, source filename, source checksum, language
    variant, prompt config/version, formatting config, lifecycle status,
    created/updated timestamps.
-   `ImageAsset`: id, project_id, source_page, reading_order,
    bounding_box, original_caption, current_filename, file_storage_key,
    checksum, caption_match_status, processing_status.
-   `AltTextVersion`: id, image_id, version_number, text,
    generation/revision instruction, prompt_version, provider/model
    metadata, created_at.
-   `Approval`: id, image_id, alt_text_version_id, reviewer identifier
    if available, approved_at.
-   `Job`: id, project_id, image_id/page range, type, status, attempts,
    error code, started_at, completed_at.
-   `Export`: id, project_id, export type, storage key/path, status,
    created_at, checksum.
-   `PromptVersion`: id, project_id or global config reference,
    assembled prompt, language variant, created_at.

Use foreign keys and transactions for approval and export state. Binary
data is stored outside the database.

## 7. MVP release gates

-   Extraction tests pass on representative PDFs.
-   Caption matching above/below images is verified.
-   Prompt preview and British/US English configuration work.
-   Incremental review is demonstrated.
-   Revision/approval integrity tests pass.
-   Worker restart and retry tests pass.
-   DOCX and audit ZIP are validated.
-   Cleanup tests prove that active/review-required assets are not
    deleted.
-   Deployment/storage/provider constraints are confirmed.

## 8. Open decisions

-   Final framework and hosting provider.
-   External AI provider and data handling.
-   Whether the editor's browser uploads to the backend or a local agent
    handles source files.
-   Exact audit ZIP format.
-   Exact DOCX template and settings.
-   Expected maximum book size and concurrency.
-   Whether multiple editors collaborate on a project.
-   Retention policy and whether editors must be able to resume after
    logout or machine restart.
