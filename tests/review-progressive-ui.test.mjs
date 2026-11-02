import test from "node:test";
import assert from "node:assert/strict";
import ExtractionProgress from "../src/web/static/extraction-progress.js";

const {
  calculateProgress,
  shouldContinuePolling,
  determineExtractionState,
  mergeImages,
  resolveActiveImageIndex,
} = ExtractionProgress;

test("Progress calculation correctly scales completed_pages / total_pages * 100", () => {
  assert.equal(calculateProgress(0, 201), 0);
  assert.equal(calculateProgress(100, 200), 50);
  assert.equal(calculateProgress(120, 201), 60);
  assert.equal(calculateProgress(201, 201), 100);
  assert.equal(calculateProgress(250, 200), 100);
  assert.equal(calculateProgress(0, 0), 0);
  assert.equal(calculateProgress(-5, 100), 0);
});

test("Empty-image batches advance progress without reporting false stalls", () => {
  const stateBatch1 = determineExtractionState({
    project_status: "extracting",
    job_status: "running",
    total_pages: 201,
    completed_pages: 5,
    total_batches: 41,
    completed_batches: 1,
    total_images: 2,
  });

  const stateBatch2Empty = determineExtractionState({
    project_status: "extracting",
    job_status: "running",
    total_pages: 201,
    completed_pages: 10,
    total_batches: 41,
    completed_batches: 2,
    total_images: 2, // Image count remained 2 because pages 6-10 had no images
  });

  assert.equal(stateBatch1.pagesText, "5 / 201");
  assert.equal(stateBatch2Empty.pagesText, "10 / 201");
  assert.equal(stateBatch2Empty.batchesText, "2 / 41");
  assert.equal(stateBatch2Empty.imagesText, "2");
  assert.equal(stateBatch2Empty.state, "in_progress");
  assert.equal(stateBatch2Empty.isTerminal, false);
});

test("Finalizing state triggers when all batches complete before full job completion", () => {
  const state = determineExtractionState({
    project_status: "extracting",
    job_status: "running",
    total_pages: 201,
    completed_pages: 201,
    total_batches: 41,
    completed_batches: 41,
    total_images: 5,
  });

  assert.equal(state.state, "finalizing");
  assert.equal(state.title, "Finalizing extracted figures…");
  assert.equal(state.percent, 99); // Does not show 100% until backend marks completed
  assert.equal(state.isTerminal, false);
});

test("Completed state reports 100% and enables dismissal", () => {
  const state = determineExtractionState({
    project_status: "ready_for_review",
    job_status: "completed",
    total_pages: 201,
    completed_pages: 201,
    total_batches: 41,
    completed_batches: 41,
    total_images: 5,
  });

  assert.equal(state.state, "completed");
  assert.equal(state.title, "Image extraction completed");
  assert.equal(state.percent, 100);
  assert.equal(state.pagesText, "201 / 201");
  assert.equal(state.imagesText, "5");
  assert.equal(state.isTerminal, true);
  assert.equal(state.canDismiss, true);
  assert.equal(state.spinner, "✓");
});

test("Failed state with zero images reports failure clearly", () => {
  const state = determineExtractionState({
    project_status: "failed",
    job_status: "failed",
    total_pages: 201,
    completed_pages: 0,
    total_batches: 41,
    completed_batches: 0,
    total_images: 0,
    error: "Corrupt PDF catalog",
  });

  assert.equal(state.state, "failed");
  assert.equal(state.title, "Extraction failed");
  assert.equal(state.percent, "Failed");
  assert.equal(state.error, "Corrupt PDF catalog");
  assert.equal(state.isTerminal, true);
});

test("Partially completed extraction with recovered images does not claim 100%", () => {
  const state = determineExtractionState({
    project_status: "failed",
    job_status: "failed",
    total_pages: 201,
    completed_pages: 100,
    total_batches: 41,
    completed_batches: 20,
    failed_batches: 1,
    total_images: 3,
    error: "Out of memory on batch 21",
  });

  assert.equal(state.state, "partial");
  assert.equal(state.title, "Extraction partially completed");
  assert.equal(state.percent, 50);
  assert.notEqual(state.percent, 100);
  assert.equal(state.imagesText, "3");
  assert.equal(state.isTerminal, true);
});

test("Polling lifecycle rules stop at terminal states and continue while active", () => {
  assert.equal(shouldContinuePolling({ project_status: "extracting", job_status: "running" }), true);
  assert.equal(shouldContinuePolling({ project_status: "queued", job_status: "queued" }), true);
  assert.equal(shouldContinuePolling({ project_status: "ready_for_review", job_status: "completed" }), false);
  assert.equal(shouldContinuePolling({ project_status: "failed", job_status: "failed" }), false);
  assert.equal(shouldContinuePolling(null), false);
});

test("Image merging prevents duplicates and reports newly added counts", () => {
  const existing = [
    { id: "img-1", page_number: 1 },
    { id: "img-2", page_number: 4 },
  ];

  const incomingWithDuplicates = [
    { id: "img-1", page_number: 1 },
    { id: "img-2", page_number: 4 },
    { id: "img-3", page_number: 43 },
  ];

  const result = mergeImages(existing, incomingWithDuplicates);
  assert.equal(result.hasChanges, true);
  assert.equal(result.addedCount, 1);
  assert.equal(result.images.length, 3);
  assert.deepEqual(result.images.map((img) => img.id), ["img-1", "img-2", "img-3"]);

  const noNewResult = mergeImages(result.images, incomingWithDuplicates);
  assert.equal(noNewResult.hasChanges, false);
  assert.equal(noNewResult.addedCount, 0);
  assert.equal(noNewResult.images.length, 3);
});

test("resolveActiveImageIndex preserves active image position across image arrivals", () => {
  const sorted = [
    { id: "img-1", page_number: 1 },
    { id: "img-2", page_number: 4 },
    { id: "img-3", page_number: 43 },
  ];

  assert.equal(resolveActiveImageIndex(sorted, "img-2"), 1);
  assert.equal(resolveActiveImageIndex(sorted, "img-3"), 2);
  assert.equal(resolveActiveImageIndex(sorted, "unknown-id"), 0);
  assert.equal(resolveActiveImageIndex([], "img-1"), -1);
});
