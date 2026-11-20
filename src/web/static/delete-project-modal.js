/**
 * Destructive Project Deletion Confirmation Modal Controller.
 * Separate from Mode A / Mode B storage cleanup: permanently deletes all
 * project assets, DOCX exports, alt text, history, and database records.
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

let deleteModalElement = null;

function ensureDeleteModalDom() {
  if (deleteModalElement && document.body.contains(deleteModalElement)) {
    return deleteModalElement;
  }

  const div = document.createElement("div");
  div.id = "deleteProjectModalBackdrop";
  div.className = "modal-backdrop";
  div.style.display = "none";
  div.setAttribute("role", "dialog");
  div.setAttribute("aria-modal", "true");
  div.setAttribute("aria-labelledby", "deleteProjectModalTitle");

  div.innerHTML = `
    <div class="modal-dialog" style="max-width: 580px; width: 90%; margin: 40px auto; background: var(--panel-bg, #1e1e24); border: 1px solid #ef4444; border-radius: 8px; box-shadow: 0 10px 25px rgba(0,0,0,0.6);">
      <div class="modal-header" style="display: flex; justify-content: space-between; align-items: flex-start; padding: 16px 20px; border-bottom: 1px solid var(--border-color, #333);">
        <div>
          <h3 id="deleteProjectModalTitle" style="margin: 0; font-size: 18px; color: #f87171;">Permanently Delete Project</h3>
          <p id="deleteProjectModalSubtitle" class="text-muted" style="margin: 4px 0 0 0; font-size: 13px;"></p>
        </div>
        <button type="button" id="closeDeleteProjectModalBtn" class="modal-close-btn" aria-label="Close dialog" style="background: none; border: none; font-size: 20px; color: #888; cursor: pointer;">&times;</button>
      </div>
      <div class="modal-body" style="padding: 18px 20px;">
        <div style="background: rgba(239,68,68,0.12); border: 1px solid #ef4444; border-radius: 6px; padding: 12px; margin-bottom: 14px; font-size: 12.5px; color: #fca5a5; line-height: 1.5;">
          <strong>Warning — Permanent &amp; Irreversible Action:</strong><br />
          All project content and exports will be permanently deleted, including the original source PDF, extracted images, generated/approved alt text, version history, and all exported DOCX files.
        </div>

        <div id="deleteProjectPreviewBox" style="background: rgba(0,0,0,0.25); border: 1px solid var(--border-color, #333); border-radius: 6px; padding: 12px; margin-bottom: 14px; font-size: 12px;">
          <div id="deleteProjectPreviewLoading">Inspecting project records and storage objects…</div>
          <div id="deleteProjectPreviewDetails" style="display: none;">
            <div style="font-weight: 600; margin-bottom: 6px; color: #f87171;">Items to be permanently deleted:</div>
            <ul id="deleteProjectManifestList" style="margin: 0; padding-left: 18px; color: #fca5a5; line-height: 1.5;"></ul>
          </div>
        </div>

        <div id="deleteProjectBlockingAlert" style="display: none; background: rgba(239,68,68,0.15); border: 1px solid #ef4444; border-radius: 6px; padding: 10px 12px; margin-bottom: 14px; color: #fca5a5; font-size: 12px;">
          <strong>Cannot delete project right now:</strong>
          <ul id="deleteProjectBlockingList" style="margin: 4px 0 0 18px;"></ul>
        </div>

        <div style="margin-bottom: 14px;">
          <label for="deleteProjectConfirmInput" style="display: block; font-size: 12.5px; font-weight: 600; margin-bottom: 6px;">
            Type <code id="deleteProjectExpectedName" style="color: #fca5a5;"></code> to confirm permanent deletion:
          </label>
          <input type="text" id="deleteProjectConfirmInput" autocomplete="off" placeholder="Type exact project name" style="width: 100%; box-sizing: border-box; padding: 8px 10px; border-radius: 4px; border: 1px solid var(--border-color, #444); background: rgba(0,0,0,0.3); color: inherit; font-size: 13px;" />
        </div>

        <div id="deleteProjectProgressBox" style="display: none; margin-bottom: 14px; padding: 10px 12px; border-radius: 6px; background: rgba(255,255,255,0.03); border: 1px solid var(--border-color, #444); font-size: 12.5px;">
          <div id="deleteProjectProgressText"></div>
          <div id="deleteProjectFailedBox" style="display: none; margin-top: 8px; padding: 8px; background: rgba(239,68,68,0.15); border: 1px solid #ef4444; border-radius: 4px;">
            <strong>Deletion incomplete — some storage objects could not be deleted:</strong>
            <ul id="deleteProjectFailedList" style="margin: 4px 0 8px 16px; word-break: break-all;"></ul>
            <button type="button" id="deleteProjectRetryBtn" class="btn-secondary btn-sm">Retry Deletion</button>
          </div>
        </div>

        <div style="display: flex; justify-content: flex-end; gap: 10px; padding-top: 8px; border-top: 1px solid var(--border-color, #333);">
          <button type="button" id="cancelDeleteProjectBtn" class="btn-secondary" style="padding: 6px 14px;">Cancel</button>
          <button type="button" id="confirmDeleteProjectBtn" class="btn-primary" disabled style="padding: 6px 16px; background: #dc2626; border-color: #dc2626;">Permanently Delete Project</button>
        </div>
      </div>
    </div>
  `;

  document.body.appendChild(div);
  deleteModalElement = div;
  return div;
}

export function openDeleteProjectModal({ projectId, projectName = "", onComplete = null }) {
  const modal = ensureDeleteModalDom();

  const subtitleEl = modal.querySelector("#deleteProjectModalSubtitle");
  const expectedNameEl = modal.querySelector("#deleteProjectExpectedName");
  const confirmInput = modal.querySelector("#deleteProjectConfirmInput");
  const closeBtn = modal.querySelector("#closeDeleteProjectModalBtn");
  const cancelBtn = modal.querySelector("#cancelDeleteProjectBtn");
  const confirmBtn = modal.querySelector("#confirmDeleteProjectBtn");

  const previewLoading = modal.querySelector("#deleteProjectPreviewLoading");
  const previewDetails = modal.querySelector("#deleteProjectPreviewDetails");
  const manifestList = modal.querySelector("#deleteProjectManifestList");
  const blockingAlert = modal.querySelector("#deleteProjectBlockingAlert");
  const blockingList = modal.querySelector("#deleteProjectBlockingList");

  const progressBox = modal.querySelector("#deleteProjectProgressBox");
  const progressText = modal.querySelector("#deleteProjectProgressText");
  const failedBox = modal.querySelector("#deleteProjectFailedBox");
  const failedList = modal.querySelector("#deleteProjectFailedList");
  const retryBtn = modal.querySelector("#deleteProjectRetryBtn");

  let expectedName = (projectName || "").trim();
  let canDelete = false;
  let isDeleting = false;
  let activeOperationId = null;
  let hasActiveExtraction = false;
  let pollTimer = null;

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  subtitleEl.textContent = expectedName ? `Project: ${expectedName}` : `Project ID: ${projectId}`;
  expectedNameEl.textContent = expectedName;
  confirmInput.value = "";
  confirmInput.disabled = false;
  confirmBtn.disabled = true;
  confirmBtn.style.display = "inline-block";
  cancelBtn.disabled = false;
  cancelBtn.textContent = "Cancel";
  previewLoading.style.display = "block";
  previewDetails.style.display = "none";
  blockingAlert.style.display = "none";
  progressBox.style.display = "none";
  failedBox.style.display = "none";

  function updateConfirmButtonState() {
    const typed = (confirmInput.value || "").trim();
    confirmBtn.disabled = isDeleting || !canDelete || !expectedName || typed !== expectedName;
  }

  confirmInput.oninput = updateConfirmButtonState;

  function closeModal() {
    if (isDeleting) return;
    stopPolling();
    modal.style.display = "none";
  }

  closeBtn.onclick = closeModal;
  cancelBtn.onclick = closeModal;

  function pollExistingOperation(opId) {
    stopPolling();
    pollTimer = setInterval(async () => {
      try {
        const res = await fetch(`/api/cleanup/${encodeURIComponent(opId)}`, {
          credentials: "include",
        });
        if (res.status === 404) {
          stopPolling();
          progressText.textContent = "Project and all associated data permanently deleted.";
          failedBox.style.display = "none";
          confirmBtn.style.display = "none";
          cancelBtn.disabled = false;
          cancelBtn.textContent = "Close";
          isDeleting = false;
          if (typeof onComplete === "function") onComplete({ id: opId, status: "completed" });
          return;
        }
        if (!res.ok) return;
        const data = await res.json();
        const op = data.operation || {};
        if (op.status === "completed") {
          stopPolling();
          progressText.textContent = "Project and all associated data permanently deleted.";
          failedBox.style.display = "none";
          confirmBtn.style.display = "none";
          cancelBtn.disabled = false;
          cancelBtn.textContent = "Close";
          isDeleting = false;
          if (typeof onComplete === "function") onComplete(op);
          return;
        }
        const expMs = op.lease_expires_at ? Date.parse(op.lease_expires_at) : 0;
        const isStale = op.status === "failed" || op.status === "pending" || !expMs || expMs <= Date.now();
        if (isStale) {
          stopPolling();
          isDeleting = false;
          cancelBtn.disabled = false;
          renderRecoverableOperation(op);
        } else {
          progressText.textContent = `Deletion in progress (status: ${op.status})…`;
        }
      } catch (_) {}
    }, 1500);
  }

  function renderRecoverableOperation(op) {
    activeOperationId = op.id;
    canDelete = false;
    confirmInput.disabled = true;
    confirmBtn.style.display = "none";
    blockingAlert.style.display = "none";
    progressBox.style.display = "block";
    const errSuffix = op.error_message ? ` — ${op.error_message}` : "";
    progressText.textContent = `Incomplete deletion found (status: ${op.status})${errSuffix}.`;
    renderFailedKeys(op.failed_blob_keys || {}, { allowResumeWithoutFailedKeys: true });
    updateConfirmButtonState();
  }

  async function loadPreview() {
    try {
      const res = await fetch(`/api/projects/${encodeURIComponent(projectId)}/delete/preview`, {
        credentials: "include",
      });
      if (!res.ok) {
        let msg = `Error ${res.status}`;
        try {
          const errData = await res.json();
          msg = errData.detail || msg;
        } catch (_) {}
        throw new Error(msg);
      }
      const data = await res.json();
      const preview = data.preview || {};
      if (preview.project_name) {
        expectedName = preview.project_name.trim();
        expectedNameEl.textContent = expectedName;
        subtitleEl.textContent = `Project: ${expectedName}`;
      }

      const recs = preview.records_to_delete || {};
      previewLoading.style.display = "none";
      previewDetails.style.display = "block";
      hasActiveExtraction = Array.isArray(preview.active_extraction_jobs) && preview.active_extraction_jobs.length > 0;
      manifestList.innerHTML = `
        <li><strong>${preview.target_blob_count || 0}</strong> storage object(s) (source PDF, images, and DOCX exports)</li>
        <li><strong>${recs.image_assets || 0}</strong> image asset record(s) &amp; alt text</li>
        <li><strong>${recs.exports || 0}</strong> DOCX export record(s)</li>
        <li><strong>${recs.jobs || 0}</strong> job(s) &amp; <strong>${recs.audit_events || 0}</strong> audit/history record(s)</li>
        ${hasActiveExtraction ? `<li><strong>Active extraction job(s)</strong> will be cancelled automatically.</li>` : ""}
      `;

      const existingOp = preview.existing_operation || null;
      if (existingOp && existingOp.id) {
        activeOperationId = existingOp.id;
        canDelete = false;
        confirmInput.disabled = true;
        confirmBtn.style.display = "none";
        if (preview.is_active_deletion) {
          blockingAlert.style.display = "none";
          progressBox.style.display = "block";
          failedBox.style.display = "none";
          progressText.textContent = `Deletion is currently running (status: ${existingOp.status}). Waiting for completion…`;
          pollExistingOperation(existingOp.id);
        } else {
          renderRecoverableOperation(existingOp);
        }
        updateConfirmButtonState();
        return;
      }

      if (!preview.can_delete || (preview.blocking_reasons && preview.blocking_reasons.length > 0)) {
        canDelete = false;
        blockingAlert.style.display = "block";
        blockingList.innerHTML = (preview.blocking_reasons || [])
          .map((r) => `<li>${escapeHtml(r)}</li>`)
          .join("");
      } else {
        canDelete = true;
        blockingAlert.style.display = "none";
      }
      updateConfirmButtonState();
    } catch (err) {
      canDelete = false;
      previewLoading.style.display = "none";
      blockingAlert.style.display = "block";
      blockingList.innerHTML = `<li>Failed to load deletion preview: ${escapeHtml(err.message)}</li>`;
      updateConfirmButtonState();
    }
  }

  function renderFailedKeys(failedMap, { allowResumeWithoutFailedKeys = false } = {}) {
    const keys = Object.keys(failedMap || {});
    if (keys.length === 0 && !allowResumeWithoutFailedKeys) {
      failedBox.style.display = "none";
      return;
    }
    failedBox.style.display = "block";
    failedList.innerHTML = keys.length > 0
      ? keys.map((k) => `<li><code>${escapeHtml(k)}</code>: ${escapeHtml(failedMap[k])}</li>`).join("")
      : "<li>Previous deletion attempt did not finish. Resume using the existing operation.</li>";
    retryBtn.disabled = false;
    retryBtn.textContent = allowResumeWithoutFailedKeys && keys.length === 0 ? "Resume Deletion" : "Retry Deletion";

    retryBtn.onclick = async () => {
      isDeleting = true;
      retryBtn.disabled = true;
      retryBtn.textContent = "Resuming…";
      cancelBtn.disabled = true;
      try {
        const res = await fetch(`/api/deletions/${encodeURIComponent(activeOperationId)}/retry`, {
          method: "POST",
          credentials: "include",
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Retry failed");
        const op = data.operation || {};
        if (data.status === "deleted" || op.status === "completed") {
          stopPolling();
          progressText.textContent = "Project and all associated data permanently deleted.";
          failedBox.style.display = "none";
          confirmBtn.style.display = "none";
          cancelBtn.disabled = false;
          cancelBtn.textContent = "Close";
          isDeleting = false;
          if (typeof onComplete === "function") onComplete(op);
        } else {
          isDeleting = false;
          cancelBtn.disabled = false;
          renderFailedKeys(op.failed_blob_keys || {}, { allowResumeWithoutFailedKeys: true });
        }
      } catch (err) {
        isDeleting = false;
        cancelBtn.disabled = false;
        progressText.textContent = `Retry error: ${err.message}`;
        retryBtn.disabled = false;
        retryBtn.textContent = "Retry Deletion";
      }
    };
  }

  confirmBtn.onclick = async () => {
    isDeleting = true;
    confirmInput.disabled = true;
    confirmBtn.disabled = true;
    cancelBtn.disabled = true;
    progressBox.style.display = "block";
    failedBox.style.display = "none";
    progressText.textContent = hasActiveExtraction
      ? "Cancelling active extraction and permanently deleting project…"
      : "Permanently deleting project storage objects and database records…";

    try {
      const res = await fetch(`/api/projects/${encodeURIComponent(projectId)}/delete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ confirm_project_name: (confirmInput.value || "").trim() }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || `Deletion failed (HTTP ${res.status})`);
      }

      const op = data.operation || {};
      activeOperationId = op.id || null;

      if (data.status === "deleted" || op.status === "completed") {
        progressText.textContent = "Project and all associated data permanently deleted.";
        confirmBtn.style.display = "none";
        cancelBtn.disabled = false;
        cancelBtn.textContent = "Close";
        isDeleting = false;
        if (typeof onComplete === "function") onComplete(op);
      } else {
        progressText.textContent = `Deletion incomplete: ${Object.keys(op.failed_blob_keys || {}).length} storage object(s) could not be deleted.`;
        renderFailedKeys(op.failed_blob_keys || {});
        cancelBtn.disabled = false;
        isDeleting = false;
      }
    } catch (err) {
      progressText.textContent = `Deletion failed: ${err.message}`;
      confirmInput.disabled = false;
      cancelBtn.disabled = false;
      isDeleting = false;
      updateConfirmButtonState();
    }
  };

  modal.style.display = "block";
  loadPreview();
}

if (typeof window !== "undefined") {
  window.openDeleteProjectModal = openDeleteProjectModal;
}
