# Product Brief --- AI-Assisted Alt-Text Publishing Pipeline

**Status:** Draft for discovery and implementation\
**Audience:** Product owner, publishing editors, engineering\
**Version:** 0.1\
**Date:** 27 October 2026

## 1. Summary

Build a web-based editorial workspace that extracts images and their
captions from digitally generated book PDFs, generates image alt text
with an AI model, and lets editors review, revise, and approve each
result. Once every required image has been resolved, the application
generates a configurable Word document and a transparency package
containing extracted images, captions, filenames, and prompt
configuration.

The system must process asynchronously: editors should be able to review
ready images while other pages and images are still being processed.

Google Colab has been used to test the existing extraction code. Colab
should remain a development/experimentation environment, not be assumed
to be a dependable production web backend. The processing code should be
refactored into reusable Python modules that can run in Colab during
development and in a proper worker process for deployment.

## 2. Problem

The current publishing workflow requires extracting images and captions,
creating files with predictable names, generating alt text, reviewing AI
output, making iterative edits, and assembling a Word document. Doing
this manually is slow and makes it difficult to maintain consistent
prompts, preserve approval history, and recover from processing
failures.

## 3. Users

-   **Editor:** uploads books, configures language and output format,
    reviews images and captions, revises alt text, approves items, and
    exports results.
-   **Administrator/operator (optional for MVP):** configures
    deployment, AI provider credentials, storage limits, and retention
    policies.

The MVP may support one editor at a time unless the product owner
confirms a need for collaboration.

## 4. Goals

1.  Reduce manual effort in extracting and preparing image alt text.
2.  Preserve the correct relationship between every extracted image and
    its caption.
3.  Allow editors to begin reviewing as soon as individual images are
    ready.
4.  Make the system transparent: editors can inspect the prompt
    configuration and download extracted assets and captions.
5.  Support British English and US English.
6.  Make final Word formatting configurable.
7.  Avoid using the relational database for image/PDF binaries.
8.  Recover from worker restarts and individual failures without losing
    approved work.

## 5. Non-goals for the initial MVP

-   Replacing a publisher's complete publishing/content-management
    system.
-   Automatically deciding that an image is decorative; the current
    requirement is that every extracted image receives alt text.
-   Full multi-user collaboration, role hierarchy, SSO, or enterprise
    audit integrations unless required by deployment.
-   Guaranteeing perfect caption matching for every complex PDF layout;
    uncertain cases must be visible and correctable.
-   Using Google Colab as a production service without confirming that
    the intended usage is permitted and reliable.

## 6. Proposed user journey

1.  Create a project and upload a digitally generated PDF.
2.  Select British English or US English.
3.  Review the assembled prompt and optionally add publisher-specific
    instructions.
4.  Start processing.
5.  The system extracts images and matches captions in page chunks.
6.  Each image becomes an independent record and is queued for AI
    generation as soon as its image/caption pair is ready.
7.  The editor reviews image, original caption, filename, and current
    alt text.
8.  The editor approves or enters a revision instruction; the revised
    draft appears alongside or in place of the prior draft with a clear
    version history.
9.  The editor can download an audit ZIP at any time for assets that
    have been extracted.
10. Once every required image has an approved alt text and no blocking
    issue remains, generate the configured Word document.
11. Verify the export, deliver it to the editor, and clean up temporary
    files according to the retention policy.

## 7. Product principles

-   **Human approval is authoritative:** AI generation never approves
    itself.
-   **Incremental work:** no need to wait for the entire book before
    reviewing the first image.
-   **Traceability:** every draft belongs to an image and prompt
    version; approval belongs to a specific draft version.
-   **Recoverability:** jobs are resumable and retries do not create
    duplicate records.
-   **Storage discipline:** binary assets live in temporary
    filesystem/object storage, not in database blobs.
-   **Transparency:** show the prompt configuration and provide an audit
    package.
-   **Factual grounding:** the AI should use the image and caption, and
    only any additional context explicitly configured for the project.

## 8. Success measures

Measure these on representative books before committing to numeric
service-level guarantees:

-   Time from upload to first image available for review.
-   Extraction and caption-match correction rate.
-   Median and 95th-percentile time for initial generation and
    revisions.
-   Percentage of AI drafts approved without revision.
-   Average editor review time per image.
-   Export success rate and missing/duplicate image rate.
-   Number of failed jobs recovered without restarting the book.
-   Temporary storage peak per book and cleanup completion rate.
-   AI cost per book.

## 9. Key risks

-   Captions above images or complex multi-column layouts may be
    mismatched.
-   PDF image objects may be reused, composed of multiple objects, or be
    vector artwork rather than a simple embedded raster image.
-   External AI calls may be blocked by office policy or may expose
    confidential content; provider policy must be confirmed.
-   Colab runtimes can disconnect and should not be the sole durable job
    coordinator.
-   Worker local disks may be ephemeral or too small for large books.
-   Filename collisions and unsafe filename characters can break
    exports.
-   AI revisions may contradict earlier approved content unless versions
    and approvals are handled carefully.

## 10. Decisions still to confirm

-   Exact output Word template and formatting options.
-   AI provider/model and whether book content may leave the publisher's
    environment.
-   Deployment target and whether internal hosting or software
    installation is permitted.
-   Maximum expected PDF size, page count, image count, and concurrent
    books.
-   Retention period for original PDFs, extracted images, audit ZIPs,
    and final DOCX.
-   Whether caption matching needs nearby page text or only the image
    and its caption.
-   Whether the application must support multiple editors working on the
    same project.
