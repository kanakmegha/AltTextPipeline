/**
 * Accessible Project Storage Lifecycle Cleanup Modal Controller.
 * Provides interactive Mode A (Keep PDF & Exports) and Mode B (Keep Exports Only)
 * previews, active job & export fencing, execution progress, and deletion retry.
 */

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

let modalElement = null;

function ensureModalDom() {
  if (modalElement && document.body.contains(modalElement)) {
    return modalElement;
  }

  const div = document.createElement("div");
  div.id = "cleanupModalBackdrop";
  div.className = "modal-backdrop";
  div.style.display = "none";
  div.setAttribute("role", "dialog");
  div.setAttribute("aria-modal", "true");
  div.setAttribute("aria-labelledby", "cleanupModalTitle");

  div.innerHTML = `
    <div class="modal-dialog" style="max-width: 620px; width: 90%; margin: 40px auto; background: var(--panel-bg, #1e1e24); border: 1px solid var(--border-color, #333); border-radius: 8px; box-shadow: 0 10px 25px rgba(0,0,0,0.5);">
      <div class="modal-header" style="display: flex; justify-content: space-between; align-items: flex-start; padding: 16px 20px; border-bottom: 1px solid var(--border-color, #333);">
        <div class="modal-title-wrap">
          <h3 id="cleanupModalTitle" style="margin: 0; font-size: 18px; color: var(--text-color, #fff);">Project Storage Cleanup</h3>
          <p id="cleanupModalSubtitle" class="text-muted" style="margin: 4px 0 0 0; font-size: 13px;"></p>
        </div>
        <button type="button" id="closeCleanupModalBtn" class="modal-close-btn" aria-label="Close dialog" style="background: none; border: none; font-size: 20px; color: #888; cursor: pointer;">&times;</button>
      </div>
      <div class="modal-body" style="padding: 18px 20px;">
        <!-- Mode Selection -->
        <div class="cleanup-mode-selector" style="margin-bottom: 16px;">
          <label style="font-weight: 600; font-size: 13px; display: block; margin-bottom: 8px;">Select Cleanup Mode:</label>
          <div style="display: flex; gap: 10px;">
            <label class="cleanup-mode-card" id="modeACard" style="flex: 1; border: 2px solid #3b82f6; background: rgba(59,130,246,0.08); padding: 10px 12px; border-radius: 6px; cursor: pointer; transition: all 0.2s ease;">
              <div style="display: flex; align-items: center; gap: 6px;">
                <input type="radio" name="cleanupModeRadio" value="extracted" checked id="modeARadio" />
                <strong style="font-size: 13px;">Mode A: Clean Extracted Data</strong>
              </div>
              <span class="text-muted" style="font-size: 11px; display: block; margin-top: 4px; line-height: 1.4;">
                Deletes extracted images and temporary batch metadata.<br />
                <strong>Preserves</strong> original PDF and all exported Word DOCX files.
              </span>
            </label>
            <label class="cleanup-mode-card" id="modeBCard" style="flex: 1; border: 1px solid var(--border-color, #444); background: rgba(255,255,255,0.02); padding: 10px 12px; border-radius: 6px; cursor: pointer; transition: all 0.2s ease;">
              <div style="display: flex; align-items: center; gap: 6px;">
                <input type="radio" name="cleanupModeRadio" value="all_source" id="modeBRadio" />
                <strong style="font-size: 13px;">Mode B: Clean Source &amp; Extracted</strong>
              </div>
              <span class="text-muted" style="font-size: 11px; display: block; margin-top: 4px; line-height: 1.4;">
                Deletes extracted images, temporary batches, and original source PDF.<br />
                <strong>Preserves</strong> all exported Word DOCX files and history.
              </span>
            </label>
          </div>
        </div>

        <!-- Preview Manifest Box -->
        <div id="cleanupPreviewContent" style="background: rgba(0,0,0,0.25); border: 1px solid var(--border-color, #333); border-radius: 6px; padding: 14px; margin-bottom: 16px;">
          <div id="cleanupPreviewLoading" style="display: flex; align-items: center; gap: 8px; font-size: 13px;">
            <span>⏳</span> <span>Inspecting project assets and preparing preview…</span>
          </div>
          <div id="cleanupPreviewDetails" style="display: none;">
            <div style="margin-bottom: 12px;">
              <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                <span style="font-weight: 600; font-size: 13px; color: #f87171;">🗑️ To be deleted:</span>
                <span id="cleanupDeleteCounts" style="font-weight: 600; font-size: 12px; color: #f87171;"></span>
              </div>
              <ul id="cleanupDeleteList" style="margin: 0; padding-left: 20px; font-size: 12px; color: #fca5a5; line-height: 1.5;"></ul>
            </div>

            <div>
              <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                <span style="font-weight: 600; font-size: 13px; color: #34d399;">🛡️ To be retained (Protected):</span>
                <span id="cleanupRetainCounts" style="font-weight: 600; font-size: 12px; color: #34d399;"></span>
              </div>
              <ul id="cleanupRetainList" style="margin: 0; padding-left: 20px; font-size: 12px; color: #6ee7b7; line-height: 1.5;"></ul>
            </div>
          </div>
        </div>

        <!-- Blocking / Warning Alert -->
        <div id="cleanupBlockingAlert" style="display: none; background: rgba(239, 68, 68, 0.15); border: 1px solid #ef4444; border-radius: 6px; padding: 12px; margin-bottom: 16px; color: #fca5a5; font-size: 12px;">
          <strong style="display: block; margin-bottom: 4px;">⚠️ Cleanup cannot proceed:</strong>
          <ul id="cleanupBlockingList" style="margin: 0; padding-left: 18px; line-height: 1.4;"></ul>
        </div>

        <!-- Progress / Status Box -->
        <div id="cleanupProgressBox" style="display: none; margin-bottom: 16px; padding: 12px; border-radius: 6px; background: rgba(255,255,255,0.03); border: 1px solid var(--border-color, #444);">
          <div style="display: flex; align-items: center; gap: 8px;">
            <span id="cleanupProgressIcon" style="font-size: 16px;">⏳</span>
            <span id="cleanupProgressText" style="font-size: 13px; font-weight: 600;"></span>
          </div>
          <div id="cleanupFailedBox" style="display: none; margin-top: 10px; padding: 10px; background: rgba(239,68,68,0.15); border: 1px solid #ef4444; border-radius: 4px; font-size: 12px;">
            <strong>Some storage objects could not be deleted:</strong>
            <ul id="cleanupFailedKeysList" style="margin: 4px 0 8px 16px; word-break: break-all;"></ul>
            <button type="button" id="cleanupRetryBtn" class="btn-secondary btn-sm" style="margin-top: 4px;">Retry Deletion</button>
          </div>
        </div>

        <!-- Action Buttons -->
        <div style="display: flex; justify-content: flex-end; gap: 10px; padding-top: 8px; border-top: 1px solid var(--border-color, #333);">
          <button type="button" id="cancelCleanupBtn" class="btn-secondary" style="padding: 6px 14px;">Cancel</button>
          <button type="button" id="confirmCleanupBtn" class="btn-primary" style="padding: 6px 16px; background: #dc2626; border-color: #dc2626;">Confirm Clean up</button>
        </div>
      </div>
    </div>
  `;

  document.body.appendChild(div);
  modalElement = div;
  return div;
}

export function openCleanupModal({ projectId, projectName = "", onComplete = null }) {
  const modal = ensureModalDom();

  const titleEl = modal.querySelector("#cleanupModalTitle");
  const subtitleEl = modal.querySelector("#cleanupModalSubtitle");
  const closeBtn = modal.querySelector("#closeCleanupModalBtn");
  const cancelBtn = modal.querySelector("#cancelCleanupBtn");
  const confirmBtn = modal.querySelector("#confirmCleanupBtn");

  const modeARadio = modal.querySelector("#modeARadio");
  const modeBRadio = modal.querySelector("#modeBRadio");
  const modeACard = modal.querySelector("#modeACard");
  const modeBCard = modal.querySelector("#modeBCard");

  const previewLoading = modal.querySelector("#cleanupPreviewLoading");
  const previewDetails = modal.querySelector("#cleanupPreviewDetails");
  const deleteCounts = modal.querySelector("#cleanupDeleteCounts");
  const deleteList = modal.querySelector("#cleanupDeleteList");
  const retainCounts = modal.querySelector("#cleanupRetainCounts");
  const retainList = modal.querySelector("#cleanupRetainList");

  const blockingAlert = modal.querySelector("#cleanupBlockingAlert");
  const blockingList = modal.querySelector("#cleanupBlockingList");

  const progressBox = modal.querySelector("#cleanupProgressBox");
  const progressIcon = modal.querySelector("#cleanupProgressIcon");
  const progressText = modal.querySelector("#cleanupProgressText");
  const failedBox = modal.querySelector("#cleanupFailedBox");
  const failedKeysList = modal.querySelector("#cleanupFailedKeysList");
  const retryBtn = modal.querySelector("#cleanupRetryBtn");

  let currentMode = "extracted";
  let activeOperationId = null;
  let isExecuting = false;

  subtitleEl.textContent = projectName ? `Project: ${projectName}` : `Project ID: ${projectId}`;
  modeARadio.checked = true;
  currentMode = "extracted";
  updateModeCardStyles();

  // Reset visibility
  previewLoading.style.display = "flex";
  previewDetails.style.display = "none";
  blockingAlert.style.display = "none";
  progressBox.style.display = "none";
  failedBox.style.display = "none";
  confirmBtn.disabled = true;
  confirmBtn.style.display = "inline-block";
  confirmBtn.textContent = "Confirm Clean up";
  cancelBtn.textContent = "Cancel";

  function updateModeCardStyles() {
    if (currentMode === "extracted") {
      modeACard.style.borderColor = "#3b82f6";
      modeACard.style.background = "rgba(59,130,246,0.1)";
      modeBCard.style.borderColor = "var(--border-color, #444)";
      modeBCard.style.background = "rgba(255,255,255,0.02)";
    } else {
      modeBCard.style.borderColor = "#ef4444";
      modeBCard.style.background = "rgba(239,68,68,0.1)";
      modeACard.style.borderColor = "var(--border-color, #444)";
      modeACard.style.background = "rgba(255,255,255,0.02)";
    }
  }

  async function loadPreview() {
    previewLoading.style.display = "flex";
    previewDetails.style.display = "none";
    blockingAlert.style.display = "none";
    confirmBtn.disabled = true;

    try {
      const res = await fetch(`/api/projects/${encodeURIComponent(projectId)}/cleanup/preview?mode=${encodeURIComponent(currentMode)}`, {
        credentials: "include",
      });

      if (!res.ok) {
        let errMessage = `Error ${res.status}`;
        try {
          const errData = await res.json();
          errMessage = errData.detail?.message || errData.detail || errMessage;
        } catch (_) {}
        throw new Error(errMessage);
      }

      const data = await res.json();
      const preview = data.preview || {};

      previewLoading.style.display = "none";
      previewDetails.style.display = "block";

      const targetBlobCount = preview.target_blob_count || 0;
      const recDelete = preview.records_to_delete || {};
      const preserved = preview.preserved_assets || {};
      const exportKeys = preserved.export_storage_keys || [];
      const sourceKey = preserved.source_storage_key || "";
      const sourceFilename = sourceKey ? sourceKey.split("/").pop() : "source.pdf";

      // Render To Be Deleted
      deleteCounts.textContent = `${targetBlobCount} storage files`;
      deleteList.innerHTML = `
        <li><strong>${recDelete.image_assets || 0}</strong> extracted image assets</li>
        <li><strong>${recDelete.job_batches || 0}</strong> extraction batch records</li>
        <li><strong>${recDelete.jobs || 0}</strong> extraction jobs</li>
        ${currentMode === "all_source" && sourceKey ? `<li><strong>1</strong> source PDF file (${escapeHtml(sourceFilename)})</li>` : `<li>Source PDF: <em>Preserved (Not deleted)</em></li>`}
      `;

      // Render To Be Retained
      retainCounts.textContent = `${exportKeys.length} DOCX export(s)`;
      const exportListHtml = exportKeys.length > 0
        ? `<li><strong>${exportKeys.length}</strong> exported Word (DOCX) files</li>`
        : `<li>Exported Word files: <em>None created yet</em></li>`;

      retainList.innerHTML = `
        ${exportListHtml}
        <li><strong>${preserved.audit_events_count || 0}</strong> editorial audit &amp; approval records</li>
        <li><strong>${preserved.ai_usage_count || 0}</strong> AI usage ledger records</li>
        ${currentMode === "extracted" && sourceKey ? `<li><strong>1</strong> original source PDF (${escapeHtml(sourceFilename)})</li>` : ""}
      `;

      // Blocking Reasons check
      if (!preview.can_cleanup || (preview.blocking_reasons && preview.blocking_reasons.length > 0)) {
        blockingAlert.style.display = "block";
        blockingList.innerHTML = (preview.blocking_reasons || [])
          .map((r) => `<li>${escapeHtml(r)}</li>`)
          .join("");
        confirmBtn.disabled = true;
      } else {
        blockingAlert.style.display = "none";
        confirmBtn.disabled = false;
      }
    } catch (err) {
      previewLoading.style.display = "none";
      blockingAlert.style.display = "block";
      blockingList.innerHTML = `<li>Failed to fetch cleanup preview: ${escapeHtml(err.message)}</li>`;
      confirmBtn.disabled = true;
    }
  }

  function closeModal() {
    if (isExecuting) return;
    modal.style.display = "none";
    document.removeEventListener("keydown", onKeyDown);
  }

  function onKeyDown(e) {
    if (e.key === "Escape") {
      closeModal();
    }
  }

  closeBtn.onclick = closeModal;
  cancelBtn.onclick = closeModal;
  document.addEventListener("keydown", onKeyDown);

  modeARadio.onchange = () => {
    currentMode = "extracted";
    updateModeCardStyles();
    loadPreview();
  };

  modeBRadio.onchange = () => {
    currentMode = "all_source";
    updateModeCardStyles();
    loadPreview();
  };

  confirmBtn.onclick = async () => {
    isExecuting = true;
    confirmBtn.disabled = true;
    cancelBtn.disabled = true;
    modeARadio.disabled = true;
    modeBRadio.disabled = true;

    progressBox.style.display = "block";
    failedBox.style.display = "none";
    progressIcon.textContent = "⏳";
    progressText.textContent = `Cleaning up project storage (${currentMode === "extracted" ? "Mode A: Extracted Data" : "Mode B: Source & Extracted Data"})…`;

    try {
      const res = await fetch(`/api/projects/${encodeURIComponent(projectId)}/cleanup/execute`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ mode: currentMode }),
      });

      const data = await res.json();

      if (!res.ok) {
        let msg = `Cleanup failed (HTTP ${res.status})`;
        if (data && data.detail) {
          if (typeof data.detail === "object" && data.detail.blocking_reasons) {
            msg = data.detail.blocking_reasons.join(" ");
          } else if (typeof data.detail === "string") {
            msg = data.detail;
          }
        }
        throw new Error(msg);
      }

      const op = data.operation || {};
      activeOperationId = op.id;

      if (op.status === "completed") {
        progressIcon.textContent = "✅";
        progressText.textContent = `Cleanup completed successfully! Extracted data purged, exports preserved.`;
        confirmBtn.style.display = "none";
        cancelBtn.disabled = false;
        cancelBtn.textContent = "Close";
        isExecuting = false;
        if (typeof onComplete === "function") {
          onComplete(op);
        }
      } else if (op.status === "failed") {
        progressIcon.textContent = "⚠️";
        progressText.textContent = `Cleanup failed with ${Object.keys(op.failed_blob_keys || {}).length} failed object(s).`;
        renderFailedKeys(op.failed_blob_keys || {});
        cancelBtn.disabled = false;
        isExecuting = false;
      }
    } catch (err) {
      progressIcon.textContent = "❌";
      progressText.textContent = `Error: ${err.message}`;
      cancelBtn.disabled = false;
      isExecuting = false;
    }
  };

  function renderFailedKeys(failedKeys) {
    const keys = Object.keys(failedKeys);
    if (keys.length === 0) {
      failedBox.style.display = "none";
      return;
    }
    failedBox.style.display = "block";
    failedKeysList.innerHTML = keys
      .map((k) => `<li><code>${escapeHtml(k)}</code>: ${escapeHtml(failedKeys[k])}</li>`)
      .join("");

    retryBtn.onclick = async () => {
      retryBtn.disabled = true;
      retryBtn.textContent = "Retrying...";
      try {
        const res = await fetch(`/api/cleanup/${encodeURIComponent(activeOperationId)}/retry`, {
          method: "POST",
          credentials: "include",
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Retry failed");
        const op = data.operation || {};
        if (op.status === "completed") {
          progressIcon.textContent = "✅";
          progressText.textContent = `Retry completed successfully! All storage objects cleaned up.`;
          failedBox.style.display = "none";
          confirmBtn.style.display = "none";
          cancelBtn.disabled = false;
          cancelBtn.textContent = "Close";
          if (typeof onComplete === "function") {
            onComplete(op);
          }
        } else {
          renderFailedKeys(op.failed_blob_keys || {});
          retryBtn.disabled = false;
          retryBtn.textContent = "Retry Deletion";
        }
      } catch (e) {
        alert(`Retry error: ${e.message}`);
        retryBtn.disabled = false;
        retryBtn.textContent = "Retry Deletion";
      }
    };
  }

  // Open the modal and load initial preview
  modal.style.display = "block";
  loadPreview();
}

if (typeof window !== "undefined") {
  window.openCleanupModal = openCleanupModal;
}
