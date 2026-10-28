# Antigravity implementation brief --- build in approval-gated phases

You are implementing an AI-assisted alt-text publishing pipeline. Work
in the existing repository. Do not replace existing working extraction
code before inspecting it. The project owner wants to test and
explicitly approve every phase before you continue.

## Product requirements

The application processes digitally generated (not scanned) book PDFs.
It extracts images and matches captions above or below them, generates
alt text using a configurable prompt and British English/US English
setting, and provides an editor review interface. The editor can approve
a draft or submit an instruction to revise it, see the new draft, and
repeat. Processing and review must happen concurrently. When all
required images have approved alt text, generate a configurable Word
document containing, for each image, the filename without extension, the
image, and `Alt Text:` followed by the approved text.

The editor must be able to inspect the assembled prompt and download a
transparency package containing extracted images, captions, manifest,
and prompt configuration. Keep binary files out of the relational
database. Use temporary file storage for PDFs, images, and intermediate
artifacts, with safe cleanup after a verified export and required
delivery/retention conditions.

The existing extraction code was tested in Google Colab and currently
handles captions below images; captions above images need to be tested
and supported. The owner will supply the existing alt-text prompt
separately; do not invent a replacement prompt. Colab may be used for
development/testing, but do not assume it is an acceptable production
web backend. Build reusable Python modules that can run in a normal
worker process.

## Non-negotiable working method

**Implement exactly one phase at a time. Stop after the phase and wait
for the owner's explicit approval before starting the next phase.**

At the start: 1. Inspect the repository structure, current README,
dependencies, tests, existing PDF extraction code, prompt files, and any
current UI/API. 2. Report a short plan and any discovered risks. 3. Do
not modify files during the initial inspection. 4. Ask the owner to
approve Phase 0/Phase 1 before making implementation changes.

For each phase: - State scope and acceptance criteria before coding. -
Make the smallest coherent changes. - Add/update tests. - Run relevant
tests and report exact results. - Summarize files changed, commands run,
known limitations, and manual test steps. - Do not claim tests passed
unless you ran them. - Do not silently change scope or proceed to the
next phase. - If a requirement is ambiguous, ask one focused question
rather than guessing. - Preserve unrelated existing code and user
changes. - Never expose secrets in logs or files. - Do not commit,
deploy, delete user data, or make paid external calls without explicit
approval.

## Proposed phases

### Phase 0 --- Read-only repository discovery (no code changes)

Inspect and report: - stack, entry points, current extraction flow,
prompt location, tests, current storage and deployment setup; - what
currently extracts images/captions and how it names files; -
dependencies and known constraints; - proposed phase plan adjusted to
the actual repo.

**Stop and wait for approval.**

### Phase 1 --- Extraction test harness and baseline

Goal: understand existing extraction before changing it. - Create a
small test/fixture strategy using synthetic or owner-provided PDFs. -
Cover caption below, caption above, multi-line caption, multiple images
on a page, multi-column layout, missing caption, duplicate
caption-derived filename, page boundary, and repeated image object if
feasible. - Add diagnostics for page number, image bounding box,
candidate caption blocks, and chosen match. - Do not redesign the UI or
AI pipeline yet. - Do not fabricate accuracy claims if representative
PDFs are unavailable. - Produce a baseline report of which cases
pass/fail.

**Acceptance:** reproducible tests or a documented manual test harness,
with baseline results. Stop for approval.

### Phase 2 --- Refactor extraction into reusable Python modules

-   Refactor notebook-style logic into importable functions/modules
    without changing behavior unnecessarily.
-   Use PyMuPDF to inspect text blocks and embedded images; avoid
    rasterising all pages by default.
-   Support captions both above and below, using coordinates, horizontal
    overlap, distance, reading order, and column boundaries where
    possible.
-   Preserve original page number and reading order.
-   Flag ambiguous matches rather than silently guessing.
-   Assign stable internal image IDs independent of filenames.
-   Sanitize caption-derived filenames and resolve collisions
    deterministically.
-   Make extraction resumable/idempotent so retries do not create
    duplicate logical image records.
-   Process configurable page chunks; start with 10--20 pages as a
    tunable default and benchmark.
-   OCR is not the default for digital PDFs; only consider it as a
    fallback when selectable text is unavailable.
-   Add tests.

**Acceptance:** extraction tests pass for supported cases; limitations
documented. Stop for approval.

### Phase 3 --- Define persistent project/image state and temporary storage

-   Design project, image asset, prompt version, alt-text version,
    approval, job, and export records.
-   Store metadata/text in the database and binaries in a per-project
    temporary filesystem/object-storage location.
-   Do not put PDF/image bytes in relational database fields.
-   Implement safe filenames/path handling, checksums if useful, and
    storage ownership.
-   Add cleanup eligibility states; do not delete source/extracted
    images needed for review or export.
-   Persist job state so worker restarts do not lose progress.
-   If the repository has no database yet, propose the smallest viable
    choice and ask before introducing a new service.

**Acceptance:** tests prove metadata/file separation, stable IDs, safe
paths, and cleanup cannot delete active/review-required assets. Stop for
approval.

### Phase 4 --- Prompt configuration and AI adapter

-   Add project-level British English (`en-GB`) / US English (`en-US`)
    setting.
-   Separate fixed base instructions from editable publisher
    instructions.
-   Add a prompt preview showing the assembled prompt.
-   The owner will supply the existing alt-text prompt; request it when
    needed and do not invent its content.
-   Store an immutable prompt/config snapshot for each generation.
-   Add a provider adapter and keep API credentials server-side.
-   Treat AI output as a draft; validate empty/malformed responses and
    failures.
-   Use image + caption as default context; additional book context must
    be explicitly configured.
-   Do not call external paid APIs in tests unless explicitly approved.

**Acceptance:** unit tests for language assembly, prompt preview,
configuration versioning, and mocked model responses. Stop for approval.

### Phase 5 --- Asynchronous job processing

-   Implement extraction jobs by page chunk and generation jobs per
    image.
-   Queue generation as soon as an image/caption pair is ready; do not
    wait for the full book.
-   Use bounded concurrency, provider-aware limits, retry with backoff,
    and idempotent job handlers.
-   Keep image states such as `extracted`, `needs_caption_review`,
    `queued`, `generating`, `ready_for_review`, `revision_queued`,
    `revising`, `approved`, `failed`, and `cancelled`.
-   Persist state in the database, not only in process memory.
-   Ensure one failed image does not force reprocessing of
    successful/approved images.
-   Add progress reporting.
-   Do not use Google Colab as the sole production job coordinator.

**Acceptance:** tests prove early per-image availability, retry
behavior, idempotency, and recovery simulation. Stop for approval.

### Phase 6 --- Review UI and iterative revisions

-   Show image, filename, original caption, current alt text, source
    page, status, and prompt version where useful.
-   Allow approve and revision-instruction actions.
-   Revisions affect only the selected image and create a new version.
-   Show the updated draft when ready and preserve prior
    versions/instructions.
-   Approval must bind to a specific version; a new revision is not
    approved automatically.
-   Allow navigation/filtering by review status.
-   Show progress while other images continue processing.
-   Keep UI responsive for large projects through
    pagination/virtualization rather than rendering every image at once.

**Acceptance:** manual test demonstrates reviewing image A while image B
is still processing; version/approval tests pass. Stop for approval.

### Phase 7 --- Audit ZIP and configurable DOCX export

-   Provide a downloadable audit ZIP with manifest, extracted images,
    caption files, and prompt configuration; do not include secrets.
-   Clearly label partial versus final packages.
-   Add output settings for ordering, image size/alignment, caption
    inclusion, spacing/page breaks, filename display, and the required
    `Alt Text:` prefix.
-   Final export requires every required image to have an approved
    current version and no blocking unresolved caption match/failure.
-   Generate DOCX from persisted approved records, not current browser
    state.
-   Validate DOCX opens and contains expected records before marking
    export successful.
-   Retain the final export until delivery/retention conditions are
    satisfied.
-   Clean temporary assets only after successful verified export and
    safe delivery handling.

**Acceptance:** tests verify order, image count, approved text only,
prefix, archive contents, export validation, and safe cleanup. Stop for
approval.

### Phase 8 --- Performance, resilience, security, and deployment

-   Benchmark representative books and report time to first reviewable
    image, extraction throughput, AI latency, peak disk/memory, export
    time, and cost where measurable.
-   Add queue-depth and failure diagnostics.
-   Test worker restart, provider timeout, disk-full, interrupted
    upload, failed export, and cleanup retry.
-   Confirm hosting/firewall and AI-provider policy before selecting
    production deployment.
-   Document environment variables and deployment steps.
-   Do not claim Google Colab is suitable for production without
    verifying current terms and runtime constraints.
-   Add upload limits, access controls, filename/path sanitization,
    secret handling, and retention policy.

**Acceptance:** a reproducible test report and deployment checklist.
Stop for approval.

## Required product behavior

-   Every extracted image needs alt text; do not automatically skip
    decorative images.
-   British/US English selection is project-level and used for initial
    generations and revisions.
-   The editor can see the assembled prompt.
-   The editor can download image/caption artifacts and prompt
    configuration.
-   Every image has a stable internal ID independent of its
    caption-derived filename.
-   AI generation and human approval are separate.
-   Processing and review run concurrently.
-   Word export is configurable and contains filename without extension,
    image, and `Alt Text:` plus approved alt text.
-   Temporary binary storage is outside the relational database.
-   Cleanup must never remove files still required for active
    processing, review, export, or recovery.

## First response expected from you

Start with **Phase 0 only**. Inspect the repository without editing any
files. Report what exists, what the current extraction code does, the
risks, and a phase-by-phase plan. Then stop and ask for approval before
changing anything.
