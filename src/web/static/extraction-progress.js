(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.ExtractionProgress = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  function calculateProgress(completedPages, totalPages) {
    const completed = Number(completedPages) || 0;
    const total = Number(totalPages) || 0;
    if (total <= 0) return 0;
    return Math.min(100, Math.max(0, Math.round((completed / total) * 100)));
  }

  function shouldContinuePolling(statusInfo) {
    if (!statusInfo) return false;
    const projectStatus = statusInfo.project_status || statusInfo.status;
    const jobStatus = statusInfo.job_status || (statusInfo.job && statusInfo.job.status);

    if (projectStatus === "ready_for_review" && (jobStatus === "completed" || !jobStatus)) {
      return false;
    }
    if (jobStatus === "failed" || projectStatus === "failed" || jobStatus === "cancelled") {
      return false;
    }
    return true;
  }

  function determineExtractionState(data) {
    if (!data) {
      return {
        state: "in_progress",
        title: "Extracting images from your document…",
        subtitle: "Extraction is progressing in the background.",
        percent: 0,
        pagesText: "0 / 0",
        batchesText: "0 / 0",
        imagesText: "0",
        noticeText: "Additional images will appear here automatically as extraction continues.",
        isTerminal: false,
        canDismiss: false,
        spinner: "⏳",
      };
    }

    const projectStatus = data.project_status || data.status;
    const jobStatus = data.job_status || (data.job && data.job.status);
    const totalPages = Number(data.total_pages || (data.job && data.job.total_pages) || 0);
    const completedPages = Number(data.completed_pages || (data.job && data.job.completed_pages) || 0);
    const totalBatches = Number(data.total_batches || (data.job && data.job.total_batches) || 0);
    const completedBatches = Number(data.completed_batches || (data.job && data.job.completed_batches) || 0);
    const failedBatches = Number(data.failed_batches || 0);
    const totalImages = Number(data.total_images ?? (data.images ? data.images.length : 0));
    const errorMsg = data.error || (data.job && data.job.error_message);

    const basePercent = calculateProgress(completedPages, totalPages);
    const isFullyComplete = projectStatus === "ready_for_review" && (jobStatus === "completed" || !jobStatus);
    const isJobFailed = jobStatus === "failed" || projectStatus === "failed";
    const isPartiallyComplete = failedBatches > 0 && completedBatches > 0;

    // State B: Extraction completed
    if (isFullyComplete) {
      return {
        state: "completed",
        title: "Image extraction completed",
        subtitle: "All document pages processed and figures grouped successfully.",
        percent: 100,
        pagesText: `${totalPages} / ${totalPages}`,
        batchesText: `${totalBatches} / ${totalBatches}`,
        imagesText: `${totalImages}`,
        noticeText: "All images are ready for review.",
        isTerminal: true,
        canDismiss: true,
        spinner: "✓",
      };
    }

    // State C or D: Failed / Partially Failed
    if (isJobFailed) {
      if (totalImages > 0) {
        return {
          state: "partial",
          title: "Extraction partially completed",
          subtitle: `Extraction stopped with an error. ${totalImages} image(s) recovered and available for review.`,
          percent: basePercent,
          pagesText: `${completedPages} / ${totalPages}`,
          batchesText: `${completedBatches} completed${failedBatches > 0 ? `, ${failedBatches} failed` : ""}`,
          imagesText: `${totalImages}`,
          noticeText: "Some pages could not be extracted.",
          isTerminal: true,
          canDismiss: true,
          spinner: "⚠️",
          error: errorMsg,
        };
      }
      return {
        state: "failed",
        title: "Extraction failed",
        subtitle: errorMsg || "The background worker encountered an error during extraction.",
        percent: "Failed",
        pagesText: `${completedPages} / ${totalPages}`,
        batchesText: `${completedBatches} / ${totalBatches}`,
        imagesText: `${totalImages}`,
        noticeText: "No images could be extracted.",
        isTerminal: true,
        canDismiss: true,
        spinner: "❌",
        error: errorMsg,
      };
    }

    // State D: Incomplete with some batch failures
    if (isPartiallyComplete) {
      return {
        state: "partial",
        title: "Extraction partially completed",
        subtitle: `${failedBatches} batch(es) encountered an error during processing.`,
        percent: basePercent,
        pagesText: `${completedPages} / ${totalPages}`,
        batchesText: `${completedBatches} of ${totalBatches} (${failedBatches} failed)`,
        imagesText: `${totalImages}`,
        noticeText: "Incomplete extraction. Some images may be missing.",
        isTerminal: false,
        canDismiss: true,
        spinner: "⚠️",
      };
    }

    // State A: In Progress / Finalizing
    const isFinalizing = totalBatches > 0 && completedBatches >= totalBatches;
    return {
      state: isFinalizing ? "finalizing" : "in_progress",
      title: isFinalizing
        ? "Finalizing extracted figures…"
        : "Extracting images from your document…",
      subtitle: isFinalizing
        ? "Correlating multi-panel figure groups and associating captions across pages…"
        : "Extraction is progressing in the background. You can begin reviewing images as they arrive.",
      percent: isFinalizing ? 99 : Math.min(99, basePercent),
      pagesText: `${completedPages} / ${totalPages}`,
      batchesText: `${completedBatches} / ${totalBatches}`,
      imagesText: `${totalImages}`,
      noticeText: isFinalizing
        ? "Finalizing document figure grouping and metadata…"
        : "Additional images will appear here automatically as extraction continues.",
      isTerminal: false,
      canDismiss: false,
      spinner: "⏳",
    };
  }

  function mergeImages(currentImages, incomingImages) {
    const existingList = Array.isArray(currentImages) ? currentImages : [];
    const incomingList = Array.isArray(incomingImages) ? incomingImages : [];

    const existingIds = new Set(existingList.map((img) => img.id));
    const newItems = incomingList.filter((img) => img && img.id && !existingIds.has(img.id));

    if (newItems.length === 0) {
      return {
        images: existingList,
        addedCount: 0,
        hasChanges: false,
      };
    }

    return {
      images: [...existingList, ...newItems],
      addedCount: newItems.length,
      hasChanges: true,
    };
  }

  function resolveActiveImageIndex(sortedImages, activeImageId) {
    if (!sortedImages || sortedImages.length === 0) return -1;
    if (!activeImageId) return 0;
    const idx = sortedImages.findIndex((img) => img && img.id === activeImageId);
    return idx !== -1 ? idx : 0;
  }

  return {
    calculateProgress,
    shouldContinuePolling,
    determineExtractionState,
    mergeImages,
    resolveActiveImageIndex,
  };
});
