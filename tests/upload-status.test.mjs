import test from "node:test";
import assert from "node:assert/strict";
import uploadStatus from "../src/web/static/upload-status.js";

const { getProjectReviewUrl } = uploadStatus;

test("ready_for_review is a successful project completion state", () => {
  assert.equal(getProjectReviewUrl("ready_for_review", "project-123"), "/review/project-123");
});

test("completed remains a successful project completion state", () => {
  assert.equal(getProjectReviewUrl("completed", "project-123"), "/review/project-123");
});

test("nonterminal and failed statuses do not navigate to review", () => {
  for (const status of ["queued", "extracting", "failed"]) {
    assert.equal(getProjectReviewUrl(status, "project-123"), null);
  }
});
