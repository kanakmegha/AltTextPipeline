/**
 * AltTextPipeline — Stage 3: Export to Word Frontend Controller
 */

document.addEventListener("DOMContentLoaded", () => {
  // Extract project_id from URL: /export/{project_id}
  const pathParts = window.location.pathname.split("/").filter(Boolean);
  const projectId = pathParts[1];

  if (!projectId) {
    alert("No project ID specified in URL. Returning to upload.");
    window.location.href = "/";
    return;
  }

  // Set up navigation links
  const stepperReviewLink = document.getElementById("stepperReviewLink");
  const backToReviewBtn = document.getElementById("backToReviewBtn");
  if (stepperReviewLink) stepperReviewLink.href = `/review/${projectId}`;
  if (backToReviewBtn) backToReviewBtn.href = `/review/${projectId}`;

  // DOM Elements
  const projectTitle = document.getElementById("projectTitle");
  const reviewSummaryBadge = document.getElementById("reviewSummaryBadge");
  const checkIconImages = document.getElementById("checkIconImages");
  const checkTextImages = document.getElementById("checkTextImages");
  const checkIconApprovals = document.getElementById("checkIconApprovals");
  const checkTextApprovals = document.getElementById("checkTextApprovals");
  const checkIconReviews = document.getElementById("checkIconReviews");
  const checkTextReviews = document.getElementById("checkTextReviews");
  const exportBlockingAlert = document.getElementById("exportBlockingAlert");
  const exportBlockingList = document.getElementById("exportBlockingList");
  const startDocxExportBtn = document.getElementById("startDocxExportBtn");
  const exportsHistoryList = document.getElementById("exportsHistoryList");

  // System & Badges
  const aiConfigBadge = document.getElementById("aiConfigBadge");
  const aiSpendBadge = document.getElementById("aiSpendBadge");
  const textSizeBtns = document.querySelectorAll(".btn-text-size");

  let currentProject = null;

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

      if (configRes.ok && aiConfigBadge) {
        const config = await configRes.json();
        const dot = '<span class="badge-dot"></span>';
        if (config.is_configured) {
          aiConfigBadge.innerHTML = `${dot} AI: ${escapeHtml(config.provider_name)}`;
          aiConfigBadge.classList.add("online");
          aiConfigBadge.classList.remove("offline");
        } else {
          aiConfigBadge.innerHTML = `${dot} AI: Offline Mock`;
          aiConfigBadge.classList.add("offline");
          aiConfigBadge.classList.remove("online");
        }
      }

      if (budgetRes.ok && aiSpendBadge) {
        const budget = await budgetRes.json();
        aiSpendBadge.textContent = `Spend: $${budget.total_spend_usd.toFixed(2)} / $${budget.max_monthly_spend_usd.toFixed(2)}`;
      }
    } catch (err) {
      console.warn("Could not fetch AI telemetry:", err);
    }
  }
  refreshAiStatus();

  // --- Load Project Details ---
  async function loadProject() {
    try {
      const res = await fetch(`/api/projects/${projectId}`);
      if (!res.ok) {
        if (res.status === 404) {
          alert(`Project "${projectId}" not found.`);
          window.location.href = "/";
          return;
        }
        throw new Error(`Failed to load project: HTTP ${res.status}`);
      }
      currentProject = await res.json();
      if (projectTitle) {
        projectTitle.textContent = currentProject.project_name || currentProject.project?.name || "Project Export";
      }
    } catch (err) {
      console.error("Error loading project:", err);
    }
  }

  // --- Fetch Export Status & Checklist ---
  async function refreshExportStatus() {
    try {
      const res = await fetch(`/api/projects/${projectId}/exports/status`);
      if (!res.ok) throw new Error("Could not fetch export status");
      const data = await res.json();

      const elig = data.eligibility || {};
      const chk = data.checklist || {
        has_images: (elig.total_images || 0) > 0,
        total_images: elig.total_images || 0,
        all_approved: ((elig.unapproved_count || 0) === 0 && (elig.total_images || 0) > 0),
        approved_count: elig.approved_count || 0,
        unapproved_count: elig.unapproved_count || 0,
        no_unresolved_reviews: ((elig.unresolved_reviews || 0) === 0),
        unresolved_reviews_count: elig.unresolved_reviews || 0,
      };
      const eligible = (data.eligible === true) || (data.can_export === true) || (elig.can_export === true);
      const blockingReasons = data.blocking_reasons || elig.blocking_reasons || [];
      const recentExports = data.recent_exports || data.all_exports || [];

      // 1. Images present
      if (chk.has_images) {
        if (checkIconImages) { checkIconImages.textContent = "✓"; checkIconImages.className = "check-icon pass"; }
        if (checkTextImages) checkTextImages.textContent = `Extracted Images Present (${chk.total_images || 0} images)`;
      } else {
        if (checkIconImages) { checkIconImages.textContent = "✕"; checkIconImages.className = "check-icon fail"; }
        if (checkTextImages) checkTextImages.textContent = "No Extracted Images Found";
      }

      // 2. Alt-text approved
      if (chk.all_approved) {
        if (checkIconApprovals) { checkIconApprovals.textContent = "✓"; checkIconApprovals.className = "check-icon pass"; }
        if (checkTextApprovals) checkTextApprovals.textContent = `All Alt-Text Approved (${chk.approved_count || 0}/${chk.total_images || 0})`;
      } else {
        if (checkIconApprovals) { checkIconApprovals.textContent = "✕"; checkIconApprovals.className = "check-icon fail"; }
        if (checkTextApprovals) checkTextApprovals.textContent = `${chk.approved_count || 0}/${chk.total_images || 0} Alt-Text Approved (${chk.unapproved_count || 0} pending)`;
      }

      // 3. Caption reviews resolved
      if (chk.no_unresolved_reviews) {
        if (checkIconReviews) { checkIconReviews.textContent = "✓"; checkIconReviews.className = "check-icon pass"; }
        if (checkTextReviews) checkTextReviews.textContent = "No Unresolved Caption Reviews";
      } else {
        if (checkIconReviews) { checkIconReviews.textContent = "✕"; checkIconReviews.className = "check-icon fail"; }
        if (checkTextReviews) checkTextReviews.textContent = `${chk.unresolved_reviews_count || 0} Unresolved Caption Reviews`;
      }

      // Review Summary Badge
      if (reviewSummaryBadge) {
        const total = chk.total_images || 0;
        const approved = chk.approved_count || 0;
        const pct = total > 0 ? Math.round((approved / total) * 100) : 0;
        reviewSummaryBadge.textContent = `Review Status: ${approved}/${total} Approved (${pct}%)`;
        if (eligible) {
          reviewSummaryBadge.classList.add("matched");
          reviewSummaryBadge.classList.remove("review");
        } else {
          reviewSummaryBadge.classList.add("review");
          reviewSummaryBadge.classList.remove("matched");
        }
      }

      // Blocking reasons
      if (eligible) {
        if (exportBlockingAlert) exportBlockingAlert.style.display = "none";
        if (startDocxExportBtn) startDocxExportBtn.disabled = false;
      } else {
        if (startDocxExportBtn) startDocxExportBtn.disabled = true;
        if (exportBlockingAlert && exportBlockingList) {
          exportBlockingAlert.style.display = "block";
          const reasons = blockingReasons;
          exportBlockingList.innerHTML = reasons.map(r => `<li>${escapeHtml(r)}</li>`).join("") +
            `<li style="margin-top: 8px;"><a href="/review/${projectId}" style="color: #818cf8; font-weight: 600;">Go to Review &amp; Edit to resolve these items ➔</a></li>`;
        }
      }

      // Recent exports
      renderExportsHistory(recentExports);
    } catch (err) {
      console.error("Error refreshing export status:", err);
    }
  }

  function renderExportsHistory(exports) {
    if (!exportsHistoryList) return;
    if (exports.length === 0) {
      exportsHistoryList.innerHTML = '<p class="text-muted" style="margin: 0; font-size: 13px;">No exports generated for this project yet.</p>';
      return;
    }

    exportsHistoryList.innerHTML = exports.map((exp, index) => {
      const dateStr = exp.created_at ? new Date(exp.created_at).toLocaleString() : "";
      const sizeVal = exp.size_bytes || exp.byte_size || 0;
      const sizeStr = sizeVal ? `(${(sizeVal / 1024).toFixed(1)} KB)` : "";
      const filename = exp.filename || (exp.file_storage_key ? exp.file_storage_key.split("/").pop() : "Report.docx");
      const figCount = (exp.total_figures_exported !== undefined && exp.total_figures_exported !== null)
        ? exp.total_figures_exported
        : ((exp.figure_count !== undefined && exp.figure_count !== null) ? exp.figure_count : 0);
      const latestBadge = index === 0 ? ' <span style="background:rgba(99,102,241,0.25);color:#818cf8;border:1px solid rgba(99,102,241,0.4);font-size:11px;padding:1px 6px;border-radius:4px;font-weight:600;margin-left:6px;">Latest</span>' : '';

      return `
        <div class="recent-project-row" style="margin-bottom: 8px;">
          <div class="recent-project-info">
            <span style="font-size: 20px;" aria-hidden="true">📄</span>
            <div>
              <div class="recent-project-name">${escapeHtml(filename)} ${sizeStr}${latestBadge}</div>
              <div class="recent-project-meta">${dateStr} · ${figCount} figures</div>
            </div>
          </div>
          <div class="recent-project-actions">
            <a href="/api/projects/${projectId}/exports/${exp.id}/download" class="btn-secondary btn-sm" download="${escapeHtml(filename)}" style="text-decoration:none;">
              ⬇ Download
            </a>
          </div>
        </div>
      `;
    }).join("");
  }

  // --- Generate & Download DOCX ---
  if (startDocxExportBtn) {
    startDocxExportBtn.addEventListener("click", async () => {
      startDocxExportBtn.disabled = true;
      const originalHtml = startDocxExportBtn.innerHTML;
      startDocxExportBtn.innerHTML = '<span>Generating Word Document...</span> <span class="gen-spinner">⏳</span>';

      try {
        const res = await fetch(`/api/projects/${projectId}/exports/docx`, {
          method: "POST"
        });

        if (!res.ok) {
          let errDetail = "Export failed";
          try {
            const errJson = await res.json();
            errDetail = errJson.detail?.message || errJson.detail || errDetail;
          } catch (_) {
            errDetail = `Export failed with HTTP ${res.status}`;
          }
          throw new Error(errDetail);
        }

        const data = await res.json();
        const downloadUrl = data.download_url || `/api/projects/${projectId}/exports/${data.export_id}/download`;

        // Trigger browser file download from the actual file download endpoint
        const dlLink = document.createElement("a");
        dlLink.style.display = "none";
        dlLink.href = downloadUrl;
        const exportFilename = data.export?.filename || "Report_alt_text.docx";
        dlLink.download = exportFilename;
        document.body.appendChild(dlLink);
        dlLink.click();
        dlLink.remove();

        // Refresh export history
        await refreshExportStatus();
      } catch (err) {
        alert(`Export Error: ${err.message}`);
      } finally {
        startDocxExportBtn.disabled = false;
        startDocxExportBtn.innerHTML = originalHtml;
      }
    });
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

  // Init
  loadProject().then(refreshExportStatus);
});
