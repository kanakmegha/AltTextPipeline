/**
 * AltTextPipeline — Stage 1: Upload & Extract Frontend Controller
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
  const uploadErrorBox = document.getElementById("uploadErrorBox");
  const recentProjectsList = document.getElementById("recentProjectsList");

  // AI & System Badges
  const aiConfigBadge = document.getElementById("aiConfigBadge");
  const aiSpendBadge = document.getElementById("aiSpendBadge");
  const textSizeBtns = document.querySelectorAll(".btn-text-size");

  let selectedFile = null;
  let useSamplePdf = false;

  // --- Accessible Text Size Controls ---
  function applyTextSize(size) {
    if (!["normal", "large", "xlarge"].includes(size)) size = "normal";
    document.documentElement.setAttribute("data-text-size", size);
    localStorage.setItem("altText_textSize", size);
    textSizeBtns.forEach(btn => {
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

  textSizeBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      applyTextSize(btn.dataset.size);
    });
  });

  // --- AI Config & Budget Telemetry ---
  async function refreshAiStatus() {
    try {
      const [configRes, budgetRes] = await Promise.all([
        fetch("/api/ai/config"),
        fetch("/api/ai/budget")
      ]);

      if (configRes.ok) {
        const config = await configRes.json();
        if (aiConfigBadge) {
          const dot = '<span class="badge-dot"></span>';
          if (config.is_configured) {
            aiConfigBadge.innerHTML = `${dot} AI: ${escapeHtml(config.provider_name)}`;
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
          aiSpendBadge.textContent = `Spend: $${budget.total_spend_usd.toFixed(2)} / $${budget.max_monthly_spend_usd.toFixed(2)}`;
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

  function handleFileSelected(file) {
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      showError("Please select a valid PDF file (.pdf).");
      return;
    }
    clearError();
    selectedFile = file;
    useSamplePdf = false;
    if (selectedFileText) {
      selectedFileText.textContent = `Selected: ${file.name} (${(file.size / (1024 * 1024)).toFixed(1)} MB)`;
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
        selectedFileText.textContent = "Selected: 40345.pdf (Local Sample Volume)";
      }
      if (pageRangeInput) pageRangeInput.value = "1, 4, 33, 46, 77";
      if (projectNameInput) projectNameInput.value = "Inspection - 40345 Sample Pages";
      if (extractBtn) extractBtn.disabled = false;
    });
  }

  // --- Start Extraction ---
  if (extractBtn) {
    extractBtn.addEventListener("click", async () => {
      if (!selectedFile && !useSamplePdf) {
        showError("Please select a PDF file first or use the sample volume.");
        return;
      }

      clearError();

      const formData = new FormData();
      if (useSamplePdf) {
        formData.append("use_sample_pdf", "true");
      } else {
        formData.append("file", selectedFile);
      }

      const pages = pageRangeInput ? pageRangeInput.value.trim() : "";
      if (pages) formData.append("pages", pages);

      const prjName = projectNameInput ? projectNameInput.value.trim() : "";
      if (prjName) formData.append("project_name", prjName);

      // Loading state
      extractBtn.disabled = true;
      if (progressContainer) progressContainer.style.display = "block";
      if (progressText) progressText.textContent = "Processing PDF and extracting image/caption pairs & figure groups...";

      try {
        const response = await fetch("/api/upload-and-extract", {
          method: "POST",
          body: formData
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
        const projectId = data.project_id || (data.project && data.project.id);
        if (!projectId) {
          throw new Error("Extraction completed but server did not return a project ID.");
        }

        // Automatic navigation to Stage 2: Review & Edit
        window.location.href = `/review/${projectId}`;
      } catch (err) {
        showError(`Extraction Error: ${err.message}`);
        extractBtn.disabled = false;
        if (progressContainer) progressContainer.style.display = "none";
      }
    });
  }

  // --- Recent Projects List ---
  async function loadRecentProjects() {
    if (!recentProjectsList) return;
    try {
      const res = await fetch("/api/projects");
      if (!res.ok) return;
      const data = await res.json();
      const projects = data.projects || [];

      if (projects.length === 0) {
        recentProjectsList.innerHTML = '<p class="text-muted" style="margin:0; font-size:13px;">No projects created yet. Upload a PDF above to begin.</p>';
        return;
      }

      recentProjectsList.innerHTML = projects.slice(0, 5).map(p => {
        const dateStr = p.created_at ? new Date(p.created_at).toLocaleDateString() : "";
        return `
          <div class="recent-project-row">
            <div class="recent-project-info">
              <span style="font-size: 18px;" aria-hidden="true">📁</span>
              <div>
                <div class="recent-project-name">${escapeHtml(p.name || "Untitled Project")}</div>
                <div class="recent-project-meta">${escapeHtml(p.source_filename || "")} · ${dateStr}</div>
              </div>
            </div>
            <div class="recent-project-actions">
              <a href="/review/${p.id}" class="btn-secondary btn-sm" style="text-decoration:none;">Review &amp; Edit</a>
              <a href="/export/${p.id}" class="btn-secondary btn-sm" style="text-decoration:none;">Export</a>
            </div>
          </div>
        `;
      }).join("");
    } catch (err) {
      console.warn("Could not load recent projects:", err);
    }
  }

  loadRecentProjects();

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
});
