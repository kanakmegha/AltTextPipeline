import { upload } from "@vercel/blob/client";
import { getProjectReviewUrl } from "./upload-status.js";
import { openCleanupModal } from "./cleanup-modal.js";
import { openDeleteProjectModal } from "./delete-project-modal.js";

/**
 * AltTextPipeline — Stage 1: Upload & Extract Frontend Controller
 * Supports direct-to-Blob upload for large PDFs with progress tracking
 * and background extraction polling.
 */

document.addEventListener("DOMContentLoaded", () => {
  // --- DOM Elements ---
  const dropzone = document.getElementById("dropzone");
  const fileInput = document.getElementById("pdfFile");
  const selectedFileText = document.getElementById("selectedFileName");
  const pageRangeInput = document.getElementById("pageRange");
  const projectNameInput = document.getElementById("projectName");
  const sampleCard = document.getElementById("sampleCard");
  const useSampleBtn = document.getElementById("useSampleBtn");
  const extractBtn = document.getElementById("startExtractBtn");
  const progressContainer = document.getElementById("progressContainer");
  const progressText = document.getElementById("progressText");
  const progressBarFill = document.getElementById("progressBarFill");
  const progressiveResults = document.getElementById("progressiveResults");
  const progressiveImageList = document.getElementById("progressiveImageList");
  const uploadErrorBox = document.getElementById("uploadErrorBox");
  const recentProjectsList = document.getElementById("recentProjectsList");

  // AI & System Badges
  const aiConfigBadge = document.getElementById("aiConfigBadge");
  const aiSpendBadge = document.getElementById("aiSpendBadge");
  const textSizeBtns = document.querySelectorAll(".btn-text-size");

  let selectedFile = null;
  let useSamplePdf = false;
  let isProcessing = false; // Prevent duplicate submissions
  let currentProjectId = null;
  let statusPollInterval = null;
  const renderedProgressImageIds = new Set();

  // --- Accessible Text Size Controls ---
  function applyTextSize(size) {
    if (!["normal", "large", "xlarge"].includes(size)) size = "normal";
    document.documentElement.setAttribute("data-text-size", size);
    localStorage.setItem("altText_textSize", size);
    textSizeBtns.forEach((btn) => {
      if (btn.dataset.size === size) {
        btn.classList.add("active");
        btn.setAttribute("aria-current", "true");
      } else {
        btn.classList.remove("active");
        btn.removeAttribute("aria-current");
      }
    });
  }

  const savedTextSize = localStorage.getItem("altText_textSize") || "normal";
  applyTextSize(savedTextSize);

  textSizeBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      applyTextSize(btn.dataset.size);
    });
  });

  // --- AI Config & Budget Telemetry ---
  async function refreshAiStatus() {
    try {
      const [configRes, budgetRes] = await Promise.all([
        fetch("/api/ai/config"),
        fetch("/api/ai/budget"),
      ]);

      if (configRes.ok) {
        const config = await configRes.json();
        if (aiConfigBadge) {
          const dot = '<span class="badge-dot"></span>';
          if (config.is_configured) {
            aiConfigBadge.innerHTML = `${dot} AI: ${
              escapeHtml(config.provider_name)
            }`;
            aiConfigBadge.classList.remove("offline");
            aiConfigBadge.classList.add("online");
          } else {
            aiConfigBadge.innerHTML = `${dot} AI: Offline Mock`;
            aiConfigBadge.classList.remove("online");
            aiConfigBadge.classList.add("offline");
          }
        }
      }

      if (budgetRes.ok) {
        const budget = await budgetRes.json();
        if (aiSpendBadge) {
          aiSpendBadge.textContent = `Spend: $${
            budget.total_spend_usd.toFixed(2)
          } / $${budget.max_monthly_spend_usd.toFixed(2)}`;
          if (budget.budget_exceeded) {
            aiSpendBadge.classList.add("spend-exceeded");
          } else {
            aiSpendBadge.classList.remove("spend-exceeded");
          }
        }
      }
    } catch (err) {
      console.warn("Could not fetch AI telemetry:", err);
    }
  }

  refreshAiStatus();

  // --- Check Sample PDF Availability ---
  async function checkSamplePdf() {
    try {
      const res = await fetch("/api/sample-pdf-available");
      if (res.ok) {
        const data = await res.json();
        if (data.available && sampleCard) {
          sampleCard.style.display = "flex";
        }
      }
    } catch (err) {
      console.warn("Could not check sample PDF:", err);
    }
  }
  checkSamplePdf();

  // --- Dropzone & File Selection ---
  if (dropzone && fileInput) {
    dropzone.addEventListener("click", (e) => {
      if (e.target !== fileInput) {
        fileInput.click();
      }
    });

    dropzone.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        fileInput.click();
      }
    });

    dropzone.addEventListener("dragover", (e) => {
      e.preventDefault();
      dropzone.classList.add("dragover");
    });

    dropzone.addEventListener("dragleave", () => {
      dropzone.classList.remove("dragover");
    });

    dropzone.addEventListener("drop", (e) => {
      e.preventDefault();
      dropzone.classList.remove("dragover");
      if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
        handleFileSelected(e.dataTransfer.files[0]);
      }
    });

    fileInput.addEventListener("change", (e) => {
      if (e.target.files && e.target.files.length > 0) {
        handleFileSelected(e.target.files[0]);
      }
    });
  }

  let maxUploadBytes = null;
  let maxUploadMb = null;
  fetch("/api/uploads/config", { credentials: "include" })
    .then((r) => (r.ok ? r.json() : null))
    .then((cfg) => {
      if (cfg && typeof cfg.max_upload_bytes === "number") {
        maxUploadBytes = cfg.max_upload_bytes;
        maxUploadMb = cfg.max_upload_mb || Math.floor(maxUploadBytes / (1024 * 1024));
      }
    })
    .catch(() => {});

  function handleFileSelected(file) {
    const isPdfName = file.name.toLowerCase().endsWith(".pdf");
    const isPdfType = !file.type || file.type === "application/pdf";
    if (!isPdfName || !isPdfType) {
      showError("Unsupported or invalid PDF: please select a valid .pdf file.");
      return;
    }
    if (file.size <= 0) {
      showError("Unsupported or invalid PDF: file is empty.");
      return;
    }
    if (maxUploadBytes && file.size > maxUploadBytes) {
      showError(`File exceeds the configured upload limit (${maxUploadMb} MB).`);
      return;
    }
    clearError();
    selectedFile = file;
    useSamplePdf = false;
    if (selectedFileText) {
      selectedFileText.textContent = `Selected: ${file.name} (${
        (file.size / (1024 * 1024)).toFixed(1)
      } MB)`;
    }
    if (extractBtn) extractBtn.disabled = false;
  }

  // --- Quick Sample Button ---
  if (useSampleBtn) {
    useSampleBtn.addEventListener("click", () => {
      clearError();
      selectedFile = null;
      useSamplePdf = true;
      if (selectedFileText) {
        selectedFileText.textContent =
          "Selected: 40345.pdf (Local Sample Volume)";
      }
      if (pageRangeInput) pageRangeInput.value = "1, 4, 33, 46, 77";
      if (projectNameInput) {
        projectNameInput.value = "Inspection - 40345 Sample Pages";
      }
      if (extractBtn) extractBtn.disabled = false;
    });
  }

  // --- Progress Helpers ---
  function setProgress(percent, text) {
    if (progressContainer) progressContainer.style.display = "block";
    if (progressText) progressText.textContent = text;
    if (progressBarFill) {
      progressBarFill.style.width = `${Math.max(0, Math.min(100, percent))}%`;
      progressBarFill.setAttribute(
        "aria-valuenow",
        Math.max(0, Math.min(100, percent)),
      );
    }
  }

  function hideProgress() {
    if (progressContainer) progressContainer.style.display = "none";
    if (progressBarFill) {
      progressBarFill.style.width = "0%";
    }
  }

  function showError(msg) {
    if (uploadErrorBox) {
      uploadErrorBox.textContent = msg;
      uploadErrorBox.style.display = "block";
    } else {
      alert(msg);
    }
  }

  function clearError() {
    if (uploadErrorBox) {
      uploadErrorBox.textContent = "";
      uploadErrorBox.style.display = "none";
    }
  }

  function escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // --- Status Polling ---
  function appendProgressImages(projectId, images) {
    if (!progressiveImageList || !Array.isArray(images)) return;
    for (const image of images) {
      if (!image.id || renderedProgressImageIds.has(image.id)) continue;
      renderedProgressImageIds.add(image.id);
      const card = document.createElement("article");
      card.className = "progressive-image-card";
      card.dataset.page = String(image.page_number || 0);
      card.dataset.placement = String(image.placement_index || 0);
      const preview = document.createElement("img");
      preview.loading = "lazy";
      preview.src = `/api/projects/${encodeURIComponent(projectId)}/images/${
        encodeURIComponent(image.id)
      }/file`;
      preview.alt = image.original_caption ||
        `Extracted image from page ${image.page_number}`;
      const description = document.createElement("p");
      description.textContent = `Page ${image.page_number}${
        image.original_caption
          ? ` · ${image.original_caption}`
          : " · No caption found"
      }`;
      card.append(preview, description);
      progressiveImageList.appendChild(card);
    }
    const ordered = [...progressiveImageList.children].sort((a, b) =>
      Number(a.dataset.page) - Number(b.dataset.page) ||
      Number(a.dataset.placement) - Number(b.dataset.placement)
    );
    ordered.forEach((card) => progressiveImageList.appendChild(card));
    if (progressiveResults && ordered.length) {
      progressiveResults.style.display = "block";
    }
  }

  async function pollProjectStatus(projectId, jobId) {
    if (statusPollInterval) {
      clearInterval(statusPollInterval);
    }

    const poll = async () => {
      try {
        const endpoint = jobId
          ? `/api/jobs/${encodeURIComponent(jobId)}/progress`
          : `/api/projects/${encodeURIComponent(projectId)}/status`;
        const res = await fetch(endpoint, {
          method: "GET",
          credentials: "include",
        });

        if (!res.ok) {
          console.warn("Status poll failed:", res.status);
          return;
        }

        const data = await res.json();
        let images = data.images || [];
        if (jobId) {
          const imagesRes = await fetch(
            `/api/jobs/${encodeURIComponent(jobId)}/images`,
            { credentials: "include" },
          );
          if (imagesRes.ok) images = (await imagesRes.json()).images || [];
        }
        appendProgressImages(projectId, images);
        const status = data.project_status || data.status;
        const jobStatus = data.job_status;

        // Open Review as soon as the first image has been persisted.
        // Extraction continues in the background.
        const hasExtractedImages = images.length > 0;

        const reviewUrl = hasExtractedImages
          ? getProjectReviewUrl("ready_for_review", projectId)
          : getProjectReviewUrl(
            status === "ready_for_review" || status === "completed"
              ? status
              : jobStatus,
            projectId,
          );

        if (reviewUrl) {
          clearInterval(statusPollInterval);
          statusPollInterval = null;
          hideProgress();

          window.location.href = reviewUrl;
        } else if (status === "failed" || jobStatus === "failed") {
          clearInterval(statusPollInterval);
          statusPollInterval = null;
          hideProgress();
          isProcessing = false;
          if (extractBtn) extractBtn.disabled = false;
          const errMsg = data.error || "Extraction failed.";
          showError(`Worker processing failure: ${errMsg}`);
        } else {
          // Still processing: queued, extracting, etc.
          const totalImages = data.total_images || 0;
          const batches = data.total_batches
            ? `Batch ${data.completed_batches || 0}/${data.total_batches}`
            : "Preparing page batches";
          const pages = data.pages_to_process
            ? ` · ${data.completed_pages || 0}/${data.pages_to_process} pages`
            : "";
          const percent = data.total_batches
            ? Math.min(
              95,
              60 +
                Math.round(
                  35 * (data.completed_batches || 0) / data.total_batches,
                ),
            )
            : 60;
          setProgress(
            percent,
            `${batches}${pages} · ${totalImages} images available as extracted…`,
          );
        }
      } catch (err) {
        console.warn("Status poll error:", err);
      }
    };
    poll();
    statusPollInterval = setInterval(poll, 2500);
  }

  function stopPolling() {
    if (statusPollInterval) {
      clearInterval(statusPollInterval);
      statusPollInterval = null;
    }
  }

  // --- Start Extraction (New Direct-to-Blob Flow) ---
  if (extractBtn) {
    extractBtn.addEventListener("click", async () => {
      if (isProcessing) return; // Prevent duplicate submissions
      if (!selectedFile && !useSamplePdf) {
        showError("Please select a PDF file first or use the sample volume.");
        return;
      }

      isProcessing = true;
      clearError();
      extractBtn.disabled = true;
      stopPolling();

      const pages = pageRangeInput ? pageRangeInput.value.trim() : "";
      const prjName = projectNameInput ? projectNameInput.value.trim() : "";

      try {
        if (useSamplePdf) {
          // --- Sample PDF path (server-side, existing endpoint) ---
          setProgress(10, "Using sample PDF volume…");

          const formData = new FormData();
          formData.append("use_sample_pdf", "true");
          if (pages) formData.append("pages", pages);
          if (prjName) formData.append("project_name", prjName);

          const response = await fetch("/api/upload-and-extract", {
            method: "POST",
            body: formData,
          });

          if (!response.ok) {
            let errorMsg = "Extraction failed";
            try {
              const errorData = await response.json();
              errorMsg = errorData.detail || errorMsg;
            } catch (_) {
              errorMsg = `Extraction failed with HTTP ${response.status}`;
            }
            throw new Error(errorMsg);
          }

          const data = await response.json();
          const projectId = data.project_id ||
            (data.project && data.project.id);
          if (!projectId) {
            throw new Error(
              "Extraction completed but server did not return a project ID.",
            );
          }

          currentProjectId = projectId;
          setProgress(100, "Extraction complete. Loading review…");
          await new Promise((r) => setTimeout(r, 500));
          window.location.href = `/review/${projectId}`;
          return;
        }

        // --- Official Vercel Blob client upload (PDF bytes go directly to Blob) ---
        if (!selectedFile) {
          throw new Error("No file selected.");
        }

        // Step 1: Authorize an upload intent. This request contains metadata only.
        setProgress(5, "Authorizing upload…");
        const authRes = await fetch("/api/uploads/authorize", {
          method: "POST",
          credentials: "include",
          headers: {
            "Content-Type": "application/json",
            // X-CSRF-Token is added by api-client.js fetch wrapper
          },
          body: JSON.stringify({
            filename: selectedFile.name,
            content_type: "application/pdf",
            size_bytes: selectedFile.size,
            project_name: prjName || undefined,
            pages: pages || undefined,
          }),
        });

        if (!authRes.ok) {
          let errorMsg = "Upload authorization failed";
          try {
            const errData = await authRes.json();
            errorMsg = errData.detail || errorMsg;
          } catch (_) {}
          if (authRes.status === 413) {
            throw new Error(`File exceeds configured upload limit: ${errorMsg}`);
          }
          if (authRes.status === 400) {
            throw new Error(errorMsg);
          }
          throw new Error(`Upload authorization failure: ${errorMsg}`);
        }

        const authData = await authRes.json();
        const uploadKey = authData.upload_key;
        if (selectedFile.size > authData.max_size_bytes) {
          throw new Error(
            `File exceeds configured upload limit (${Math.floor(authData.max_size_bytes / (1024 * 1024))} MB).`,
          );
        }
        const csrfResponse = await fetch("/api/auth/csrf-token", {
          credentials: "include",
        });
        if (!csrfResponse.ok) {
          throw new Error("Upload authorization failure: could not prepare a secure upload.");
        }
        const csrfData = await csrfResponse.json();
        let blobResult;
        try {
          blobResult = await upload(authData.pathname, selectedFile, {
            access: "private",
            handleUploadUrl: authData.handle_upload_url,
            clientPayload: JSON.stringify({
              upload_key: uploadKey,
              csrf_token: csrfData.csrf_token,
            }),
            contentType: "application/pdf",
            multipart: selectedFile.size > 5 * 1024 * 1024,
            onUploadProgress: ({ percentage }) =>
              setProgress(
                10 + Math.round(percentage * 0.4),
                `Uploading ${escapeHtml(selectedFile.name)} (${
                  Math.round(percentage)
                }%)…`,
              ),
          });
        } catch (blobErr) {
          throw new Error(`Blob upload failure: ${blobErr?.message || "direct upload failed"}`);
        }
        if (blobResult.pathname !== authData.pathname) {
          throw new Error("Blob upload failure: returned an unexpected object path.");
        }

        setProgress(55, "Verifying private upload and creating project…");

        // Step 3: Complete upload and create project
        const completeRes = await fetch(`/api/uploads/${uploadKey}/complete`, {
          method: "POST",
          credentials: "include",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            project_name: prjName || undefined,
            pages: pages || undefined,
          }),
        });

        if (!completeRes.ok) {
          let errorMsg = "Project creation failed";
          try {
            const errData = await completeRes.json();
            errorMsg = errData.detail || errorMsg;
          } catch (_) {}
          throw new Error(errorMsg);
        }

        const completeData = await completeRes.json();
        const projectId = completeData.project_id;
        if (!projectId) {
          throw new Error(
            "Project creation succeeded but no project ID returned.",
          );
        }

        currentProjectId = projectId;

        if (completeData.already_completed) {
          // Project already exists, just poll status
          setProgress(60, "Project already exists. Checking status…");
        } else {
          setProgress(60, "Project created. Queuing extraction…");
        }

        // Step 4: Poll for extraction completion
        setProgress(70, "Waiting for extraction to start…");
        pollProjectStatus(projectId, completeData.job_id);
      } catch (err) {
        stopPolling();
        hideProgress();
        isProcessing = false;
        extractBtn.disabled = false;
        showError(`Error: ${err.message}`);
      }
    });
  }

  // --- Existing Projects List (Paginated) ---
  const PROJECTS_PAGE_SIZE = 10;
  let currentProjectsPage = 0;

  async function loadRecentProjects(page = currentProjectsPage) {
    if (!recentProjectsList) return;
    currentProjectsPage = Math.max(0, page);
    const offset = currentProjectsPage * PROJECTS_PAGE_SIZE;
    try {
      const res = await fetch(
        `/api/projects?limit=${PROJECTS_PAGE_SIZE}&offset=${offset}`,
        { credentials: "include" },
      );
      if (!res.ok) return;
      const data = await res.json();
      const projects = data.projects || [];
      const totalCount = typeof data.total_count === "number"
        ? data.total_count
        : projects.length;

      if (totalCount === 0) {
        recentProjectsList.innerHTML =
          '<p class="text-muted" style="margin:0; font-size:13px;">No projects created yet. Upload a PDF above to begin.</p>';
        return;
      }

      if (projects.length === 0 && currentProjectsPage > 0) {
        return loadRecentProjects(currentProjectsPage - 1);
      }

      const rowsHtml = projects.map((p) => {
        const dateStr = p.created_at
          ? new Date(p.created_at).toLocaleDateString()
          : "";
        return `
          <div class="recent-project-row">
            <div class="recent-project-info">
              <span style="font-size: 18px;" aria-hidden="true">📁</span>
              <div>
                <div class="recent-project-name">${
          escapeHtml(p.name || "Untitled Project")
        }</div>
                <div class="recent-project-meta">${
          escapeHtml(p.source_filename || "")
        } · ${dateStr}</div>
              </div>
            </div>
            <div class="recent-project-actions">
              <a href="/review/${p.id}" class="btn-secondary btn-sm" style="text-decoration:none;">Review &amp; Edit</a>
              <a href="/export/${p.id}" class="btn-secondary btn-sm" style="text-decoration:none;">Export</a>
              <button type="button" class="btn-secondary btn-sm btn-cleanup-trigger" data-project-id="${p.id}" data-project-name="${escapeHtml(p.name || "Untitled Project")}">Clean up</button>
              <button type="button" class="btn-secondary btn-sm btn-delete-project-trigger" data-project-id="${p.id}" data-project-name="${escapeHtml(p.name || "Untitled Project")}" style="color:#f87171; border-color:rgba(248,113,113,0.4);">Delete Project</button>
            </div>
          </div>
        `;
      }).join("");

      const totalPages = Math.max(1, Math.ceil(totalCount / PROJECTS_PAGE_SIZE));
      const paginationHtml = totalPages > 1
        ? `
          <div class="projects-pagination" style="display:flex; justify-content:space-between; align-items:center; margin-top:12px; padding-top:10px; border-top:1px solid var(--border-color, #333); font-size:12px;">
            <span class="text-muted">Showing ${offset + 1}–${offset + projects.length} of ${totalCount} projects</span>
            <div style="display:flex; gap:8px; align-items:center;">
              <button type="button" class="btn-secondary btn-sm btn-projects-prev" ${currentProjectsPage === 0 ? "disabled" : ""}>← Previous</button>
              <span class="text-muted">Page ${currentProjectsPage + 1} of ${totalPages}</span>
              <button type="button" class="btn-secondary btn-sm btn-projects-next" ${currentProjectsPage + 1 >= totalPages ? "disabled" : ""}>Next →</button>
            </div>
          </div>
        `
        : "";

      recentProjectsList.innerHTML = rowsHtml + paginationHtml;
    } catch (err) {
      console.warn("Could not load recent projects:", err);
    }
  }

  loadRecentProjects(0);

  if (recentProjectsList) {
    recentProjectsList.addEventListener("click", (e) => {
      const prevBtn = e.target.closest(".btn-projects-prev");
      if (prevBtn && !prevBtn.disabled) {
        loadRecentProjects(currentProjectsPage - 1);
        return;
      }
      const nextBtn = e.target.closest(".btn-projects-next");
      if (nextBtn && !nextBtn.disabled) {
        loadRecentProjects(currentProjectsPage + 1);
        return;
      }
      const deleteBtn = e.target.closest(".btn-delete-project-trigger");
      if (deleteBtn) {
        openDeleteProjectModal({
          projectId: deleteBtn.dataset.projectId,
          projectName: deleteBtn.dataset.projectName,
          onComplete: () => loadRecentProjects(currentProjectsPage),
        });
        return;
      }
      const btn = e.target.closest(".btn-cleanup-trigger");
      if (!btn) return;
      const projectId = btn.dataset.projectId;
      const projectName = btn.dataset.projectName;
      openCleanupModal({
        projectId,
        projectName,
        onComplete: () => loadRecentProjects(currentProjectsPage),
      });
    });
  }

  // Cleanup polling on page unload
  window.addEventListener("beforeunload", () => {
    stopPolling();
  });
});
