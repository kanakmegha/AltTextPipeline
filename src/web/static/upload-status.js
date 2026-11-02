function getProjectReviewUrl(status, projectId) {
  if (status !== "completed" && status !== "ready_for_review") return null;
  return `/review/${encodeURIComponent(projectId)}`;
}

module.exports = { getProjectReviewUrl };
