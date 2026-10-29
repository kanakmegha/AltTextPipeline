/**
 * AltTextPipeline — Stage 2: Review & Edit Frontend Controller
 */

document.addEventListener("DOMContentLoaded", () => {
  // Extract project_id from URL: /review/{project_id}
  const pathParts = window.location.pathname.split("/").filter(Boolean);
  const projectId = pathParts[1];

  if (!projectId) {
    alert("No project ID specified in URL. Returning to upload.");
    window.location.href = "/";
    return;
  }

  // Setup Stepper & Nav links
  const stepperExportLink = document.getElementById("stepperExportLink");
  const continueExportBtn = document.getElementById("continueExportBtn");
  if (stepperExportLink) stepperExportLink.href = `/export/${projectId}`;
  if (continueExportBtn) continueExportBtn.href = `/export/${projectId}`;

  // Top Level / Summary Elements
  const projectTitle = document.getElementById("projectTitle");
  const totalCountEl = document.getElementById("totalCount");
  const matchedCountEl = document.getElementById("matchedCount");
  const groupCountEl = document.getElementById("groupCount");
  const reviewCountEl = document.getElementById("reviewCount");
  const reviewProgressBarText = document.getElementById("reviewProgressBarText");

  // System & Badges
  const aiConfigBadge = document.getElementById("aiConfigBadge");
  const aiSpendBadge = document.getElementById("aiSpendBadge");
  const textSizeBtns = document.querySelectorAll(".btn-text-size");

  // Direct Review Workspace Container
  const reviewWorkspaceSection = document.getElementById("reviewWorkspaceSection");

  // Figure Groups Management Modal Elements
  const manageGroupsBtn = document.getElementById("manageGroupsBtn");
  const figureGroupsModal = document.getElementById("figureGroupsModal");
  const figureGroupsCloseBtn = document.getElementById("figureGroupsCloseBtn");
  const figureGroupsBody = document.getElementById("figureGroupsBody");

  // Multi-Selection Dock & Context Menu
  const selectionDock = document.getElementById("selectionDock");
  const selectionCountLabel = document.getElementById("selectionCountLabel");
  const selectionPagesLabel = document.getElementById("selectionPagesLabel");
  const groupSelectedBtn = document.getElementById("groupSelectedBtn");
  const clearSelectionBtn = document.getElementById("clearSelectionBtn");
  const imageContextMenu = document.getElementById("imageContextMenu");
  const contextMenuGroupBtn = document.getElementById("contextMenuGroupBtn");
  const contextMenuInspectBtn = document.getElementById("contextMenuInspectBtn");

  // Caption Grouping Modal Elements
  const captionGroupingModal = document.getElementById("captionGroupingModal");
  const groupDecisionCloseBtn = document.getElementById("groupDecisionCloseBtn");
  const groupDecisionCancelBtn = document.getElementById("groupDecisionCancelBtn");
  const groupDecisionConfirmBtn = document.getElementById("groupDecisionConfirmBtn");
  const groupDecisionImagesList = document.getElementById("groupDecisionImagesList");
  const groupDecisionCount = document.getElementById("groupDecisionCount");
  const groupDecisionOptions = document.getElementById("groupDecisionOptions");
  const groupFinalCaptionInput = document.getElementById("groupFinalCaptionInput");
  const groupDecisionError = document.getElementById("groupDecisionError");
  const groupConfirmSpinner = document.getElementById("groupConfirmSpinner");

  // Large Review Workspace Elements
  const rwImageHeading = document.getElementById("rwImageHeading");
  const rwPageBadge = document.getElementById("rwPageBadge");
  const rwFilenameBadge = document.getElementById("rwFilenameBadge");
  const rwStatusBadge = document.getElementById("rwStatusBadge");
  const rwGroupBadge = document.getElementById("rwGroupBadge");
  const rwProgressLabel = document.getElementById("rwProgressLabel");
  const rwUnresolvedIssuesCount = document.getElementById("rwUnresolvedIssuesCount");
  const rwProgressBarFill = document.getElementById("rwProgressBarFill");
  const rwPrevBtn = document.getElementById("rwPrevBtn");
  const rwNextBtn = document.getElementById("rwNextBtn");
  const rwImageCounter = document.getElementById("rwImageCounter");
  const rwImagePreview = document.getElementById("rwImagePreview");
  const rwFocusPreviewBtn = document.getElementById("rwFocusPreviewBtn");
  const rwDimensionsText = document.getElementById("rwDimensionsText");
  const rwReviewAlertBox = document.getElementById("rwReviewAlertBox");
  const rwReviewReasonsList = document.getElementById("rwReviewReasonsList");
  const rwCaptionExpandBtn = document.getElementById("rwCaptionExpandBtn");
  const rwCaptionBlock = document.getElementById("rwCaptionBlock");
  const rwCaptionText = document.getElementById("rwCaptionText");
  const rwCaptionEditInput = document.getElementById("rwCaptionEditInput");
  const rwSaveCaptionBtn = document.getElementById("rwSaveCaptionBtn");
  const rwResolveCaptionBtn = document.getElementById("rwResolveCaptionBtn");
  const rwVersionBadge = document.getElementById("rwVersionBadge");
  const rwLangSelect = document.getElementById("rwLangSelect");
  const rwContaminationAlert = document.getElementById("rwContaminationAlert");
  const rwCleanDraftBtn = document.getElementById("rwCleanDraftBtn");
  const rwDraftTextarea = document.getElementById("rwDraftTextarea");
  const rwWordCountBadge = document.getElementById("rwWordCountBadge");
  const rwVersionSelect = document.getElementById("rwVersionSelect");
  const rwRevisionInput = document.getElementById("rwRevisionInput");
  const rwReviseBtn = document.getElementById("rwReviseBtn");
  const rwReviseSpinner = document.getElementById("rwReviseSpinner");
  const rwErrorAlert = document.getElementById("rwErrorAlert");
  const rwGenerateDraftBtn = document.getElementById("rwGenerateDraftBtn");
  const rwRegenerateDraftBtn = document.getElementById("rwRegenerateDraftBtn");
  const rwGenSpinner = document.getElementById("rwGenSpinner");
  const rwGenBtnText = document.getElementById("rwGenBtnText");
  const rwSaveDraftBtn = document.getElementById("rwSaveDraftBtn");
  const rwApproveBtn = document.getElementById("rwApproveBtn");

  // Modals
  const inspectionModal = document.getElementById("inspectionModal");
  const modalCloseBtn = document.getElementById("modalCloseBtn");
  const focusedPreviewModal = document.getElementById("focusedPreviewModal");
  const focusedPreviewTitle = document.getElementById("focusedPreviewTitle");
  const focusedPreviewImg = document.getElementById("focusedPreviewImg");
  const focusedPreviewCaption = document.getElementById("focusedPreviewCaption");
  const focusedPreviewCloseBtn = document.getElementById("focusedPreviewCloseBtn");

  // State
  let currentProject = null;
  let currentImages = [];
  let currentGroups = [];
  let currentFilter = "all";
  let currentPdfPage = 1;
  let skipEmptyPages = true;
  let activeView = "reader"; // 'reader' or 'review'
  let currentReviewIndex = 0;
  let currentReviewImageVersions = [];
  let currentReviewImageApproval = null;
  let selectedModalImage = null;
  let aiConfig = null;

  const selectedImageIds = new Set();
  let isSelectionMode = false;
  let activeContextMenuImage = null;

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

  // --- AI Config & Telemetry ---
  async function refreshAiStatus() {
    try {
      const [configRes, budgetRes] = await Promise.all([
        fetch("/api/ai/config"),
        fetch("/api/ai/budget")
      ]);

      if (configRes.ok && aiConfigBadge) {
        aiConfig = await configRes.json();
        const dot = '<span class="badge-dot"></span>';
        const isConfigured = Boolean(aiConfig.is_configured || aiConfig.token_status === "configured");
        const isMock = Boolean(aiConfig.is_mock || (!isConfigured && aiConfig.token_status !== "configured"));
        const providerName = aiConfig.provider_name || (aiConfig.model ? `${aiConfig.provider || "HF"} (${aiConfig.model.split("/").pop()})` : "Active");

        if (isConfigured && !isMock) {
          aiConfigBadge.innerHTML = `${dot} AI: ${escapeHtml(providerName)}`;
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
        const tracker = budget.tracker || budget;
        const total = typeof tracker.total_spend_usd === "number" ? tracker.total_spend_usd : 0;
        const maxSpend = typeof tracker.max_monthly_spend_usd === "number" ? tracker.max_monthly_spend_usd : (aiConfig?.max_monthly_spend_usd || 2.0);
        aiSpendBadge.textContent = `Spend: $${total.toFixed(2)} / $${maxSpend.toFixed(2)}`;
      }
    } catch (err) {
      console.warn("Could not fetch AI telemetry:", err);
    }
  }
  refreshAiStatus();

  // Helper: Default prompt
  function getDefaultPromptForLang(lang) {
    if (!aiConfig) {
      const langName = lang === "en-US" ? "American English" : "British English";
      return `Based on the provided caption, generate a neutral-toned, item-by-item alt-text description in ${langName} that details all visual elements and text, keeping the total length to 45–50 words.`;
    }
    return lang === "en-US" ? aiConfig.default_prompt_us : aiConfig.default_prompt_gb;
  }

  // Helper: Count words
  function countWords(text) {
    if (!text) return 0;
    const words = text.trim().split(/\s+/).filter(w => w.length > 0);
    return words.length;
  }

  // --- Load Full Project State ---
  async function loadProject() {
    try {
      const [projRes, imgRes, grpRes] = await Promise.all([
        fetch(`/api/projects/${projectId}`),
        fetch(`/api/projects/${projectId}/images`),
        fetch(`/api/projects/${projectId}/groups`),
      ]);

      if (!projRes.ok) {
        if (projRes.status === 404) {
          alert(`Project "${projectId}" not found.`);
          window.location.href = "/";
          return;
        }
        throw new Error(`Failed to load project: HTTP ${projRes.status}`);
      }

      const projData = await projRes.json();
      const imgData = imgRes.ok ? await imgRes.json() : { images: [] };
      const grpData = grpRes.ok ? await grpRes.json() : { groups: [] };

      currentProject = {
        project_id: projectId,
        project_name: projData.project_name || projData.project?.name || "Project Review",
        total_pages: projData.total_pages || 1,
        stats: projData.stats || {}
      };

      currentImages = imgData.images || projData.images || [];
      currentGroups = grpData.groups || projData.groups || [];

      // Update Summary Header
      updateHeaderCounts();
      updateOverallProgress();
      await renderReviewWorkspace();
    } catch (err) {
      console.error("Error loading project state:", err);
    }
  }

  function updateHeaderCounts() {
    if (!currentImages) return;
    if (projectTitle && currentProject) projectTitle.textContent = currentProject.project_name;
    if (totalCountEl) totalCountEl.textContent = currentImages.length;
    if (matchedCountEl) matchedCountEl.textContent = currentImages.filter(img => boolCaption(img) && !img.needs_caption_review).length;
    if (groupCountEl) groupCountEl.textContent = currentGroups ? currentGroups.length : 0;
    if (reviewCountEl) reviewCountEl.textContent = currentImages.filter(img => img.needs_caption_review).length;
  }

  async function reloadProjectState() {
    if (!currentProject) return;
    try {
      const [imgRes, grpRes] = await Promise.all([
        fetch(`/api/projects/${currentProject.project_id}/images`),
        fetch(`/api/projects/${currentProject.project_id}/groups`),
      ]);
      const imgData = await imgRes.json();
      const grpData = await grpRes.json();
      currentImages = imgData.images || [];
      currentGroups = grpData.groups || [];

      updateHeaderCounts();
      updateOverallProgress();
      await renderReviewWorkspace();
      renderFigureGroupsModal();
    } catch (e) {
      console.warn("Could not reload project state:", e);
    }
  }

  // --- Figure Groups Management Modal Handlers ---
  function openFigureGroupsModal() {
    renderFigureGroupsModal();
    if (figureGroupsModal) figureGroupsModal.style.display = "flex";
  }

  function closeFigureGroupsModal() {
    if (figureGroupsModal) figureGroupsModal.style.display = "none";
  }

  if (manageGroupsBtn) manageGroupsBtn.addEventListener("click", openFigureGroupsModal);
  if (figureGroupsCloseBtn) figureGroupsCloseBtn.addEventListener("click", closeFigureGroupsModal);
  if (figureGroupsModal) {
    figureGroupsModal.addEventListener("click", (e) => {
      if (e.target === figureGroupsModal) closeFigureGroupsModal();
    });
  }
  if (rwGroupBadge) {
    rwGroupBadge.addEventListener("click", () => {
      openFigureGroupsModal();
    });
  }

  function renderFigureGroupsModal() {
    if (!figureGroupsBody) return;
    if (!currentGroups || currentGroups.length === 0) {
      figureGroupsBody.innerHTML = `<p class="text-muted" style="text-align: center; padding: 32px; font-size: 15px;">No multi-image figure groups in this project.</p>`;
      return;
    }

    figureGroupsBody.innerHTML = "";
    currentGroups.forEach(grp => {
      const card = document.createElement("div");
      card.className = "group-manage-card";
      const isConfirmed = grp.review_status === "confirmed";
      const isCrossPage = Boolean(grp.is_cross_page);
      const memberImages = grp.images || currentImages.filter(img => img.group_id === grp.id);

      const statusBadgeClass = isConfirmed ? "matched" : "review";
      const statusBadgeText = isConfirmed ? "✓ CONFIRMED" : (isCrossPage ? "CROSS-PAGE CANDIDATE" : "CANDIDATE GROUP");

      let memberThumbsHtml = "";
      memberImages.forEach(m => {
        const fileUrl = `/api/projects/${currentProject.project_id}/images/${m.id}/file`;
        const page = m.source_page_number ?? m.page_number ?? 1;
        const panel = m.panel_label ? ` (${m.panel_label})` : "";
        memberThumbsHtml += `
          <div class="group-thumb-item" style="cursor: pointer;" title="Jump to this image in Review" onclick="window.jumpToImageReview('${m.id}')">
            <img class="group-thumb-img" src="${fileUrl}" alt="Thumbnail" />
            <span class="group-thumb-meta">Page ${page}${panel}</span>
            <button class="btn-group-action remove" type="button" style="margin-top: 4px; font-size: 11px; padding: 2px 6px;" onclick="event.stopPropagation(); window.removeImageFromGroup('${grp.id}', '${m.id}')">Remove</button>
          </div>
        `;
      });

      card.innerHTML = `
        <div class="group-manage-header">
          <div class="group-manage-title">
            <span>${escapeHtml(grp.figure_label || "Figure Group")}</span>
            <span class="status-pill ${statusBadgeClass}" style="font-size: 12px;">${statusBadgeText}</span>
          </div>
          <span style="font-size: 13px; color: var(--text-muted);">${memberImages.length} images · Pages ${grp.pages_spanned || grp.primary_page || 1}</span>
        </div>
        <div class="group-manage-caption">
          <strong>Shared Caption:</strong> ${escapeHtml(grp.caption || "(No caption)")}
        </div>
        <div class="group-thumbnails-row">
          ${memberThumbsHtml}
        </div>
        <div class="group-manage-actions">
          <button class="btn-group-action confirm" type="button" onclick="window.confirmGroup('${grp.id}')">✓ Confirm Group Caption</button>
          <button class="btn-group-action split" type="button" onclick="window.splitGroup('${grp.id}')">✂️ Split Group</button>
        </div>
      `;
      figureGroupsBody.appendChild(card);
    });
  }

  window.jumpToImageReview = (imageId) => {
    closeFigureGroupsModal();
    const sorted = getSortedReviewImages();
    const idx = sorted.findIndex(img => img.id === imageId);
    if (idx !== -1) {
      currentReviewIndex = idx;
      renderReviewWorkspace();
    }
  };

  // --- Overall Progress Calculation ---
  function updateOverallProgress() {
    updateHeaderCounts();
    if (!currentImages || currentImages.length === 0) {
      if (reviewProgressBarText) reviewProgressBarText.textContent = "Progress: 0/0 Approved (0%)";
      if (rwProgressLabel) rwProgressLabel.textContent = "Review Progress: 0 of 0 Approved";
      if (rwProgressBarFill) rwProgressBarFill.style.width = "0%";
      return;
    }
    const total = currentImages.length;
    const approved = currentImages.filter(img => img.processing_status === "approved").length;
    const pct = Math.round((approved / total) * 100);

    const unresolvedIssues = currentImages.filter(img => {
      const reasons = typeof img.review_reasons === "string" ? JSON.parse(img.review_reasons || "[]") : (img.review_reasons || []);
      return img.needs_caption_review || reasons.length > 0;
    }).length;

    if (reviewProgressBarText) {
      reviewProgressBarText.textContent = `Progress: ${approved}/${total} Approved (${pct}%)`;
    }
    if (rwProgressLabel) {
      rwProgressLabel.textContent = `Review Progress: ${approved} of ${total} Approved (${pct}%)`;
    }
    if (rwProgressBarFill) {
      rwProgressBarFill.style.width = `${pct}%`;
    }
    if (rwUnresolvedIssuesCount) {
      if (unresolvedIssues > 0) {
        rwUnresolvedIssuesCount.style.display = "inline-flex";
        rwUnresolvedIssuesCount.textContent = `${unresolvedIssues} Unresolved Issue${unresolvedIssues > 1 ? "s" : ""}`;
      } else {
        rwUnresolvedIssuesCount.style.display = "none";
      }
    }
  }

  function getSortedReviewImages() {
    return [...currentImages].sort((a, b) => {
      const pA = a.source_page_number ?? a.page_number ?? 1;
      const pB = b.source_page_number ?? b.page_number ?? 1;
      if (pA !== pB) return pA - pB;
      const idxA = a.placement_index ?? 0;
      const idxB = b.placement_index ?? 0;
      return idxA - idxB;
    });
  }

  // Helper: safe no-op for gallery rendering in direct Review Mode
  function renderGallery() {}

  // --- Multi-Selection Dock & Context Menu ---
  function updateSelectionDock() {
    if (!selectionDock) return;
    const count = selectedImageIds.size;
    if (count === 0) {
      selectionDock.style.display = "none";
      isSelectionMode = false;
      return;
    }

    selectionDock.style.display = "block";
    const selectedImgs = currentImages.filter(img => selectedImageIds.has(img.id));
    const pages = [...new Set(selectedImgs.map(img => img.page_number))].sort((a, b) => a - b);

    if (selectionCountLabel) {
      selectionCountLabel.textContent = `${count} ${count === 1 ? 'image' : 'images'} selected`;
    }

    if (selectionPagesLabel) {
      const pageCount = pages.length;
      if (pageCount === 0) {
        selectionPagesLabel.textContent = "";
      } else if (pageCount === 1) {
        selectionPagesLabel.textContent = `on page ${pages[0]}`;
      } else {
        selectionPagesLabel.textContent = `across ${pageCount} pages (p. ${pages.join(", ")})`;
      }
    }

    if (groupSelectedBtn) {
      if (count >= 2) {
        groupSelectedBtn.disabled = false;
        groupSelectedBtn.title = "Group selected images and assign caption";
      } else {
        groupSelectedBtn.disabled = true;
        groupSelectedBtn.title = "Select at least 2 images to group";
      }
    }
  }

  function toggleSelectImage(imageId) {
    if (selectedImageIds.has(imageId)) {
      selectedImageIds.delete(imageId);
      if (selectedImageIds.size === 0) isSelectionMode = false;
    } else {
      selectedImageIds.add(imageId);
      isSelectionMode = true;
    }
    updateSelectionDock();
    renderGallery();
  }

  function clearSelection() {
    selectedImageIds.clear();
    isSelectionMode = false;
    updateSelectionDock();
    renderGallery();
  }

  if (clearSelectionBtn) clearSelectionBtn.addEventListener("click", clearSelection);
  if (groupSelectedBtn) groupSelectedBtn.addEventListener("click", () => openCaptionGroupingModal());

  function openContextMenu(img, x, y, triggerEl = null) {
    activeContextMenuImage = img;
    if (!imageContextMenu) return;

    imageContextMenu.style.display = "block";
    const menuWidth = 200;
    const menuHeight = 90;
    const posX = (x + menuWidth > window.innerWidth) ? (window.innerWidth - menuWidth - 10) : x;
    const posY = (y + menuHeight > window.innerHeight) ? (y - menuHeight) : y;

    imageContextMenu.style.left = `${Math.max(10, posX)}px`;
    imageContextMenu.style.top = `${Math.max(10, posY)}px`;

    const handleDocClick = (e) => {
      if (!imageContextMenu.contains(e.target) && (!triggerEl || !triggerEl.contains(e.target))) {
        closeContextMenu();
        document.removeEventListener("click", handleDocClick);
        document.removeEventListener("keydown", handleDocEsc);
      }
    };
    const handleDocEsc = (e) => {
      if (e.key === "Escape") {
        closeContextMenu();
        document.removeEventListener("click", handleDocClick);
        document.removeEventListener("keydown", handleDocEsc);
      }
    };
    setTimeout(() => {
      document.addEventListener("click", handleDocClick);
      document.addEventListener("keydown", handleDocEsc);
    }, 10);
  }

  function closeContextMenu() {
    if (imageContextMenu) imageContextMenu.style.display = "none";
    activeContextMenuImage = null;
  }

  if (contextMenuGroupBtn) {
    contextMenuGroupBtn.addEventListener("click", () => {
      if (activeContextMenuImage) {
        selectedImageIds.add(activeContextMenuImage.id);
        isSelectionMode = true;
        updateSelectionDock();
        renderGallery();
      }
      closeContextMenu();
    });
  }

  if (contextMenuInspectBtn) {
    contextMenuInspectBtn.addEventListener("click", () => {
      if (activeContextMenuImage) {
        openInspectionModal(activeContextMenuImage);
      }
      closeContextMenu();
    });
  }

  // --- Caption Grouping Modal Logic (Cases A, B, C) ---
  function openCaptionGroupingModal() {
    if (!currentProject || selectedImageIds.size < 2) return;

    if (groupDecisionError) {
      groupDecisionError.style.display = "none";
      groupDecisionError.textContent = "";
    }

    const selectedImgs = currentImages.filter(img => selectedImageIds.has(img.id));
    if (groupDecisionCount) groupDecisionCount.textContent = selectedImgs.length;

    if (groupDecisionImagesList) {
      groupDecisionImagesList.innerHTML = "";
      selectedImgs.forEach(img => {
        const item = document.createElement("div");
        item.className = "group-decision-item";
        const filename = (img.suggested_filename || "image.png").replace(/^.*[\\\/]/, "");
        const cap = (img.original_caption || "").trim();
        const capDisplay = cap ? `"${escapeHtml(cap)}"` : `<span style="color:var(--text-muted);">(No caption)</span>`;
        item.innerHTML = `
          <div class="group-decision-item-meta">
            <span class="page-badge">Page ${img.page_number}</span>
            <span title="${escapeHtml(filename)}">${escapeHtml(filename)}</span>
          </div>
          <div class="group-decision-item-caption" title="${escapeHtml(cap || 'No caption')}">
            ${capDisplay}
          </div>
        `;
        groupDecisionImagesList.appendChild(item);
      });
    }

    const existingCaptions = selectedImgs
      .map(img => (img.original_caption || "").trim())
      .filter(c => c.length > 0);
    const distinctCaptions = [...new Set(existingCaptions)];

    let currentMode = "custom";
    let mergedProposal = "";
    let appliedExistingCaption = "";

    if (groupDecisionOptions) {
      groupDecisionOptions.innerHTML = "";

      if (distinctCaptions.length > 1) {
        // Case A: Multiple distinct existing captions
        currentMode = "merged";
        mergedProposal = distinctCaptions.join(" / ");
        groupDecisionOptions.innerHTML = `
          <label class="decision-radio-card active" data-mode="merged">
            <input type="radio" name="groupCaptionChoice" value="merged" checked />
            <div class="decision-radio-info">
              <span class="decision-radio-title">Editable merged caption proposal</span>
              <span class="decision-radio-desc">Combine distinct captions into one shared proposal</span>
            </div>
          </label>
          <label class="decision-radio-card" data-mode="custom">
            <input type="radio" name="groupCaptionChoice" value="custom" />
            <div class="decision-radio-info">
              <span class="decision-radio-title">Provide custom caption</span>
              <span class="decision-radio-desc">Enter an original custom caption for all grouped images</span>
            </div>
          </label>
        `;
        if (groupFinalCaptionInput) groupFinalCaptionInput.value = mergedProposal;

      } else if (distinctCaptions.length === 1) {
        // Case B: Exactly one distinct existing caption
        currentMode = "applied_existing";
        appliedExistingCaption = distinctCaptions[0];
        groupDecisionOptions.innerHTML = `
          <label class="decision-radio-card active" data-mode="applied_existing">
            <input type="radio" name="groupCaptionChoice" value="applied_existing" checked />
            <div class="decision-radio-info">
              <span class="decision-radio-title">Apply existing caption to all selected images</span>
              <span class="decision-radio-desc">"${escapeHtml(appliedExistingCaption)}"</span>
            </div>
          </label>
          <label class="decision-radio-card" data-mode="custom">
            <input type="radio" name="groupCaptionChoice" value="custom" />
            <div class="decision-radio-info">
              <span class="decision-radio-title">Provide custom caption</span>
              <span class="decision-radio-desc">Enter a custom caption instead of applying the existing one</span>
            </div>
          </label>
        `;
        if (groupFinalCaptionInput) groupFinalCaptionInput.value = appliedExistingCaption;

      } else {
        // Case C: No existing captions
        currentMode = "custom";
        groupDecisionOptions.innerHTML = `
          <label class="decision-radio-card active" data-mode="custom">
            <input type="radio" name="groupCaptionChoice" value="custom" checked />
            <div class="decision-radio-info">
              <span class="decision-radio-title">Provide custom caption</span>
              <span class="decision-radio-desc">No existing captions found. Enter a custom caption below (do not auto-invent).</span>
            </div>
          </label>
        `;
        if (groupFinalCaptionInput) groupFinalCaptionInput.value = "";
      }

      // Radio changes
      const cards = groupDecisionOptions.querySelectorAll(".decision-radio-card");
      cards.forEach(card => {
        card.addEventListener("click", () => {
          cards.forEach(c => c.classList.remove("active"));
          card.classList.add("active");
          const radio = card.querySelector("input[type='radio']");
          if (radio) {
            radio.checked = true;
            currentMode = radio.value;
            if (currentMode === "merged" && groupFinalCaptionInput) groupFinalCaptionInput.value = mergedProposal;
            else if (currentMode === "applied_existing" && groupFinalCaptionInput) groupFinalCaptionInput.value = appliedExistingCaption;
            else if (currentMode === "custom" && groupFinalCaptionInput && distinctCaptions.length === 0) groupFinalCaptionInput.value = "";
          }
        });
      });
    }

    if (captionGroupingModal) captionGroupingModal.style.display = "flex";
  }

  function closeCaptionGroupingModal() {
    if (captionGroupingModal) captionGroupingModal.style.display = "none";
  }

  if (groupDecisionCloseBtn) groupDecisionCloseBtn.addEventListener("click", closeCaptionGroupingModal);
  if (groupDecisionCancelBtn) groupDecisionCancelBtn.addEventListener("click", closeCaptionGroupingModal);

  if (groupDecisionConfirmBtn) {
    groupDecisionConfirmBtn.addEventListener("click", async () => {
      if (!currentProject) return;

      const finalCaption = (groupFinalCaptionInput ? groupFinalCaptionInput.value : "").trim();
      if (!finalCaption) {
        if (groupDecisionError) {
          groupDecisionError.textContent = "Please provide a non-empty caption for the group.";
          groupDecisionError.style.display = "block";
        }
        return;
      }

      const selectedRadio = groupDecisionOptions ? groupDecisionOptions.querySelector("input[name='groupCaptionChoice']:checked") : null;
      const decisionMode = selectedRadio ? selectedRadio.value : "custom";

      groupDecisionConfirmBtn.disabled = true;
      if (groupConfirmSpinner) groupConfirmSpinner.style.display = "inline";
      if (groupDecisionCancelBtn) groupDecisionCancelBtn.disabled = true;
      if (groupDecisionError) groupDecisionError.style.display = "none";

      try {
        const payload = {
          image_ids: Array.from(selectedImageIds),
          caption: finalCaption,
          decision_mode: decisionMode,
          figure_label: null,
          caption_position: "below",
          allow_reassignment: false
        };

        const res = await fetch(`/api/projects/${currentProject.project_id}/groups/create-from-selection`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });

        const data = await res.json();
        if (!res.ok) {
          throw new Error(data.detail?.message || data.detail || "Failed to create group");
        }

        clearSelection();
        closeCaptionGroupingModal();
        await reloadProjectState();
      } catch (err) {
        if (groupDecisionError) {
          groupDecisionError.textContent = err.message || "Failed to group images";
          groupDecisionError.style.display = "block";
        }
      } finally {
        groupDecisionConfirmBtn.disabled = false;
        if (groupConfirmSpinner) groupConfirmSpinner.style.display = "none";
        if (groupDecisionCancelBtn) groupDecisionCancelBtn.disabled = false;
      }
    });
  }

  // --- Render Extracted-Image Page Reader Gallery ---
  function renderGallery() {
    return;

    const pageImages = currentImages.filter(img => img.page_number === currentPdfPage);

    if (pageImages.length === 0) {
      const pagesWithImages = getPagesWithImages();
      const nextWithImages = pagesWithImages.find(p => p > currentPdfPage);
      const prevWithImages = [...pagesWithImages].reverse().find(p => p < currentPdfPage);

      let actionHtml = "";
      if (nextWithImages !== undefined) {
        actionHtml = `<button class="btn-secondary" type="button" onclick="window.readerGoToPage(${nextWithImages})">Jump to Page ${nextWithImages} (next with images) →</button>`;
      } else if (prevWithImages !== undefined) {
        actionHtml = `<button class="btn-secondary" type="button" onclick="window.readerGoToPage(${prevWithImages})">Jump to Page ${prevWithImages} (prev with images) ←</button>`;
      }

      galleryGrid.innerHTML = `
        <div class="reader-empty-page" role="status">
          <div class="reader-empty-icon" aria-hidden="true">📄</div>
          <h3 class="reader-empty-title">Page ${currentPdfPage} has no extracted images</h3>
          <p class="reader-empty-desc">This PDF page contains only body text, tables, or non-image elements.</p>
          <div class="reader-empty-actions">
            ${actionHtml}
          </div>
        </div>
      `;
      return;
    }

    const filtered = pageImages.filter(img => {
      const hasCap = boolCaption(img);
      const isGrouped = Boolean(img.group_id) || img.association_type === "group_matched";
      const isSingle = hasCap && !isGrouped && !img.needs_caption_review;

      if (currentFilter === "single") return isSingle;
      if (currentFilter === "grouped") return isGrouped;
      if (currentFilter === "review") return img.needs_caption_review;
      if (currentFilter === "uncaptioned") return !hasCap;
      return true;
    });

    if (filtered.length === 0) {
      galleryGrid.innerHTML = `
        <div class="reader-empty-page" role="status">
          <p class="reader-empty-desc">No images on Page ${currentPdfPage} match the filter "${currentFilter}".</p>
        </div>
      `;
      return;
    }

    filtered.sort((a, b) => (a.placement_index || 0) - (b.placement_index || 0));

    filtered.forEach(img => {
      const card = document.createElement("div");
      card.className = "image-card";
      card.dataset.id = img.id;

      const isSelected = selectedImageIds.has(img.id);
      if (isSelected) card.classList.add("selected");

      const isGrouped = Boolean(img.group_id) || img.association_type === "group_matched";
      const isApproved = img.processing_status === "approved";
      const isMatched = !img.needs_caption_review && boolCaption(img);

      let statusClass = "review";
      let statusLabel = "NEEDS REVIEW";
      if (isApproved) {
        statusClass = "matched";
        statusLabel = "APPROVED";
      } else if (isMatched) {
        statusClass = "matched";
        statusLabel = isGrouped ? "GROUP MATCHED" : "MATCHED";
      } else if (!boolCaption(img)) {
        statusClass = "review";
        statusLabel = "UNCAPTIONED";
      }

      const imgSrc = `/api/projects/${currentProject.project_id}/images/${img.id}/file`;
      const captionText = img.original_caption || "No caption detected";
      const captionClass = img.original_caption ? "has-caption" : "no-caption";

      const panelBadge = img.panel_label ? `<span class="page-badge" style="background:#4f46e5;">PANEL ${escapeHtml(img.panel_label)}</span>` : "";

      let groupBadge = "";
      if (img.group_id) {
        const grp = currentGroups.find(g => g.id === img.group_id);
        const grpLabel = grp?.figure_label || "Group";
        groupBadge = `<span class="page-badge" style="background:#6366f1;">${escapeHtml(grpLabel)}</span>`;
      }

      card.innerHTML = `
        <div class="card-preview-wrapper">
          <img class="card-img" src="${imgSrc}" alt="${img.suggested_filename}" loading="lazy" />
          <div class="card-badges-overlay">
            <div class="card-badges-left">
              <button class="card-select-toggle ${isSelected ? 'selected' : ''}" type="button" aria-label="${isSelected ? 'Deselect image' : 'Select image for grouping'}" title="${isSelected ? 'Deselect' : 'Select for grouping'}">
                <span class="card-checkbox-icon">${isSelected ? '✓' : ''}</span>
              </button>
              <span class="page-badge" title="Source PDF Page ${img.page_number}">PAGE ${img.page_number}</span>
              ${panelBadge}
              ${groupBadge}
            </div>
            <div class="card-badges-right">
              <span class="status-pill ${statusClass}">${statusLabel}</span>
              <button class="card-menu-btn" type="button" aria-haspopup="true" aria-expanded="false" aria-label="Image actions" title="More actions">⋮</button>
            </div>
          </div>
        </div>
        <div class="card-content">
          <h4 class="card-filename" title="${img.suggested_filename}">${img.suggested_filename}</h4>
          <div class="card-caption-block ${captionClass}">
            ${escapeHtml(captionText)}
          </div>
          <div class="card-footer">
            <span>Pos: ${img.caption_position || "None"}</span>
            <button class="view-btn">Inspect &amp; Alt Text</button>
          </div>
        </div>
      `;

      // Select toggle button click
      const selectBtn = card.querySelector(".card-select-toggle");
      selectBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        toggleSelectImage(img.id);
      });

      // 3-dot menu button click
      const menuBtn = card.querySelector(".card-menu-btn");
      menuBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        const rect = menuBtn.getBoundingClientRect();
        openContextMenu(img, rect.right, rect.bottom + 4, menuBtn);
      });

      // Inspect & Alt Text button click
      const viewBtn = card.querySelector(".view-btn");
      viewBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        openInspectionModal(img);
      });

      // Right-click context menu
      card.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        openContextMenu(img, e.clientX, e.clientY);
      });

      // Keyboard support on card
      card.addEventListener("keydown", (e) => {
        if ((e.shiftKey && e.key === "F10") || e.key === "ContextMenu") {
          e.preventDefault();
          const rect = card.getBoundingClientRect();
          openContextMenu(img, rect.left + 20, rect.top + 20);
        }
      });

      // Card click
      card.addEventListener("click", (e) => {
        if (e.target.closest(".card-menu-btn") || e.target.closest(".card-select-toggle") || e.target.closest(".view-btn")) {
          return;
        }
        if (isSelectionMode) {
          toggleSelectImage(img.id);
        } else {
          // Open in Large Review Workspace
          const sorted = getSortedReviewImages();
          const idx = sorted.findIndex(i => i.id === img.id);
          if (idx >= 0) currentReviewIndex = idx;
          switchView("review");
        }
      });

      galleryGrid.appendChild(card);
    });
  }

  function boolCaption(img) {
    return Boolean(img.original_caption && img.original_caption.trim().length > 0);
  }

  // --- Large Individual-Image Review Workspace ---
  async function renderReviewWorkspace() {
    const sorted = getSortedReviewImages();
    if (!sorted || sorted.length === 0) return;

    if (currentReviewIndex < 0) currentReviewIndex = 0;
    if (currentReviewIndex >= sorted.length) currentReviewIndex = sorted.length - 1;

    const img = sorted[currentReviewIndex];
    if (!img) return;

    updateOverallProgress();

    const pageNum = img.source_page_number ?? img.page_number ?? 1;
    const cleanFilename = (img.original_filename || "extracted_asset.png").replace(/^.*[\\\/]/, "");
    const baseName = cleanFilename.replace(/\.[^/.]+$/, "");

    if (rwImageHeading) rwImageHeading.textContent = `Image Review — ${baseName}`;
    if (rwPageBadge) rwPageBadge.textContent = `Page ${pageNum}`;
    if (rwFilenameBadge) rwFilenameBadge.textContent = cleanFilename;
    if (rwImageCounter) rwImageCounter.textContent = `${currentReviewIndex + 1} of ${sorted.length}`;

    if (rwPrevBtn) rwPrevBtn.disabled = (currentReviewIndex === 0);
    if (rwNextBtn) rwNextBtn.disabled = (currentReviewIndex >= sorted.length - 1);

    if (rwGroupBadge) {
      if (img.group_id) {
        rwGroupBadge.style.display = "inline-block";
        rwGroupBadge.textContent = img.panel_label ? `Group (${img.panel_label})` : "Grouped Figure";
      } else {
        rwGroupBadge.style.display = "none";
      }
    }

    const isApproved = img.processing_status === "approved";
    if (rwStatusBadge) {
      if (isApproved) {
        rwStatusBadge.className = "status-pill matched";
        rwStatusBadge.textContent = "✓ Approved";
      } else if (img.needs_caption_review) {
        rwStatusBadge.className = "status-pill review";
        rwStatusBadge.textContent = "Needs Review";
      } else if (img.processing_status === "drafted") {
        rwStatusBadge.className = "status-pill review";
        rwStatusBadge.textContent = "Draft Ready (Unapproved)";
      } else {
        rwStatusBadge.className = "status-pill review";
        rwStatusBadge.textContent = "Pending Review";
      }
    }

    const fileUrl = `/api/projects/${currentProject.project_id}/images/${img.id}/file`;
    if (rwImagePreview) {
      rwImagePreview.src = fileUrl;
      rwImagePreview.alt = img.original_caption || `Image extracted from page ${pageNum}`;
    }
    if (rwDimensionsText) {
      rwDimensionsText.textContent = `Page ${pageNum} • ${img.image_format || "PNG"} • ${img.width || "?"}×${img.height || "?"}px`;
    }

    const reasons = typeof img.review_reasons === "string" ? JSON.parse(img.review_reasons || "[]") : (img.review_reasons || []);
    const hasCaptionIssue = img.needs_caption_review || reasons.length > 0;
    if (rwReviewAlertBox && rwReviewReasonsList) {
      if (hasCaptionIssue) {
        rwReviewAlertBox.style.display = "block";
        rwReviewReasonsList.innerHTML = reasons.length > 0
          ? reasons.map(r => `<li>${escapeHtml(r)}</li>`).join("")
          : `<li>Caption association or text requires human verification before final approval.</li>`;
      } else {
        rwReviewAlertBox.style.display = "none";
        rwReviewReasonsList.innerHTML = "";
      }
    }
    if (rwResolveCaptionBtn) {
      rwResolveCaptionBtn.style.display = hasCaptionIssue ? "inline-block" : "none";
    }

    const captionText = img.original_caption || "";
    if (rwCaptionText) {
      rwCaptionText.textContent = captionText || "(No caption detected for this image)";
      if (!captionText) {
        rwCaptionBlock.classList.remove("has-caption");
      } else {
        rwCaptionBlock.classList.add("has-caption");
      }
    }
    if (rwCaptionEditInput) rwCaptionEditInput.value = captionText;
    if (rwCaptionExpandBtn) rwCaptionExpandBtn.style.display = captionText.length > 140 ? "inline-block" : "none";

    // Load alt-text versions
    try {
      const res = await fetch(`/api/projects/${currentProject.project_id}/images/${img.id}/versions`);
      if (res.ok) {
        const data = await res.json();
        currentReviewImageVersions = data.versions || [];
        currentReviewImageApproval = data.approval || null;
        populateReviewVersionsDropdown(data.versions, data.latest_version, data.approval, img);
      }
    } catch (e) {
      console.warn("Could not load versions:", e);
    }
  }

  function populateReviewVersionsDropdown(versions, latestVersion, approval, img) {
    if (!rwVersionSelect) return;
    rwVersionSelect.innerHTML = "";

    if (!versions || versions.length === 0) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "No versions generated yet";
      rwVersionSelect.appendChild(opt);
      if (rwDraftTextarea) rwDraftTextarea.value = "";
      if (rwVersionBadge) rwVersionBadge.textContent = "No Draft";
      if (rwGenBtnText) rwGenBtnText.textContent = "✨ Generate Alt Text";
      if (rwApproveBtn) {
        rwApproveBtn.innerHTML = "<span>✓ Approve Alt Text</span>";
        rwApproveBtn.className = "btn-approve-primary";
      }
      updateRwWordCount();
      return;
    }

    let activeVersion = latestVersion || versions[versions.length - 1];

    versions.forEach(v => {
      const opt = document.createElement("option");
      opt.value = v.id;
      const isApprovedVer = approval && approval.alt_text_version_id === v.id && img && img.processing_status === "approved";
      const flag = isApprovedVer ? " ★ Approved" : "";
      const source = v.provider === "manual" ? "Client Edit" : (v.model ? v.model.split("/").pop() : "AI");
      opt.textContent = `v${v.version_number} (${source})${flag}`;
      if (activeVersion && v.id === activeVersion.id) opt.selected = true;
      rwVersionSelect.appendChild(opt);
    });

    if (activeVersion && rwDraftTextarea) {
      rwDraftTextarea.value = activeVersion.text || "";
      if (rwGenBtnText) rwGenBtnText.textContent = "✨ Regenerate Alt Text";
      const isAppr = approval && approval.alt_text_version_id === activeVersion.id && img && img.processing_status === "approved";
      if (rwVersionBadge) {
        rwVersionBadge.textContent = isAppr ? `v${activeVersion.version_number} (Approved)` : `v${activeVersion.version_number} (Draft)`;
      }
      if (rwApproveBtn) {
        if (isAppr) {
          rwApproveBtn.innerHTML = "<span>✓ Approved</span>";
          rwApproveBtn.className = "btn-secondary";
        } else {
          rwApproveBtn.innerHTML = "<span>✓ Approve Alt Text</span>";
          rwApproveBtn.className = "btn-approve-primary";
        }
      }
    }
    updateRwWordCount();
  }

  function isContaminatedDraft(text) {
    if (!text) return false;
    const patterns = [
      /Need\s+\d+[\u2013-]\d+\s+words/i,
      /\bDraft\s*:/i,
      /\bRevised\s*:/i,
      /\bTrim\s*:/i,
      /\bShorter\s*:/i,
      /\bCount\s+words\s*:/i,
      /\bCount\s*:\s*[A-Za-z0-9]+/i,
      /\bWord\(\d+\)/i,
      /incorporating\s+revision\s*:/i,
      /=\s*\d+\s*\.\s*Need\s+\d+/i
    ];
    return patterns.some(p => p.test(text));
  }

  function updateRwWordCount() {
    if (!rwDraftTextarea || !rwWordCountBadge) return;
    const text = rwDraftTextarea.value;
    const count = countWords(text);

    // Check contamination
    if (rwContaminationAlert) {
      if (isContaminatedDraft(text)) {
        rwContaminationAlert.style.display = "block";
      } else {
        rwContaminationAlert.style.display = "none";
      }
    }

    if (count === 0) {
      rwWordCountBadge.className = "word-count-badge";
      rwWordCountBadge.textContent = "0 words (target: 45–50)";
      return;
    }

    if (count >= 45 && count <= 50) {
      rwWordCountBadge.className = "word-count-badge valid";
      rwWordCountBadge.textContent = `✓ ${count} words (target: 45–50)`;
    } else {
      rwWordCountBadge.className = "word-count-badge warning";
      const diff = count < 45 ? `${45 - count} below min` : `${count - 50} above max`;
      rwWordCountBadge.textContent = `⚠️ ${count} words (${diff})`;
    }
  }

  if (rwDraftTextarea) rwDraftTextarea.addEventListener("input", updateRwWordCount);

  if (rwVersionSelect) {
    rwVersionSelect.addEventListener("change", () => {
      const vid = rwVersionSelect.value;
      const ver = currentReviewImageVersions.find(v => v.id === vid);
      if (ver && rwDraftTextarea) {
        rwDraftTextarea.value = ver.text || "";
        const sorted = getSortedReviewImages();
        const img = sorted[currentReviewIndex];
        const isAppr = currentReviewImageApproval && currentReviewImageApproval.alt_text_version_id === ver.id && img && img.processing_status === "approved";
        if (rwVersionBadge) {
          rwVersionBadge.textContent = isAppr ? `v${ver.version_number} (Approved)` : `v${ver.version_number} (Draft)`;
        }
        if (rwApproveBtn) {
          if (isAppr) {
            rwApproveBtn.innerHTML = "<span>✓ Approved</span>";
            rwApproveBtn.className = "btn-secondary";
          } else {
            rwApproveBtn.innerHTML = "<span>✓ Approve Alt Text</span>";
            rwApproveBtn.className = "btn-approve-primary";
          }
        }
        updateRwWordCount();
      }
    });
  }

  if (rwPrevBtn) {
    rwPrevBtn.addEventListener("click", () => {
      if (currentReviewIndex > 0) {
        currentReviewIndex--;
        renderReviewWorkspace();
      }
    });
  }

  if (rwNextBtn) {
    rwNextBtn.addEventListener("click", () => {
      const sorted = getSortedReviewImages();
      if (currentReviewIndex < sorted.length - 1) {
        currentReviewIndex++;
        renderReviewWorkspace();
      }
    });
  }

  if (rwCaptionExpandBtn) {
    rwCaptionExpandBtn.addEventListener("click", () => {
      if (rwCaptionBlock.classList.contains("expanded")) {
        rwCaptionBlock.classList.remove("expanded");
        rwCaptionExpandBtn.textContent = "Expand";
      } else {
        rwCaptionBlock.classList.add("expanded");
        rwCaptionExpandBtn.textContent = "Collapse";
      }
    });
  }

  if (rwFocusPreviewBtn) {
    rwFocusPreviewBtn.addEventListener("click", () => {
      const sorted = getSortedReviewImages();
      const img = sorted[currentReviewIndex];
      if (!img) return;
      focusedPreviewImg.src = `/api/projects/${currentProject.project_id}/images/${img.id}/file`;
      focusedPreviewTitle.textContent = `Focused Inspection — ${img.original_filename || "Image"}`;
      focusedPreviewCaption.textContent = img.original_caption ? `Caption: ${img.original_caption}` : "(No caption detected)";
      focusedPreviewModal.style.display = "flex";
    });
  }

  if (focusedPreviewCloseBtn) {
    focusedPreviewCloseBtn.addEventListener("click", () => {
      focusedPreviewModal.style.display = "none";
    });
  }
  if (focusedPreviewModal) {
    focusedPreviewModal.addEventListener("click", (e) => {
      if (e.target === focusedPreviewModal) focusedPreviewModal.style.display = "none";
    });
  }

  if (rwSaveCaptionBtn) {
    rwSaveCaptionBtn.addEventListener("click", async () => {
      const sorted = getSortedReviewImages();
      const img = sorted[currentReviewIndex];
      if (!img || !currentProject) return;

      const newCap = (rwCaptionEditInput.value || "").trim();
      rwSaveCaptionBtn.disabled = true;
      try {
        let res;
        try {
          res = await fetch(`/api/projects/${currentProject.project_id}/images/${img.id}/caption`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ caption: newCap })
          });
        } catch (netErr) {
          throw new Error("Network connection error: " + netErr.message);
        }

        if (!res.ok) {
          let errDetail = "Could not update caption";
          try {
            const errData = await res.json();
            errDetail = errData.detail || errDetail;
          } catch (_) {}
          throw new Error(errDetail);
        }

        const data = await res.json();
        Object.assign(img, data.image);
        const idx = currentImages.findIndex(i => i.id === img.id);
        if (idx >= 0) Object.assign(currentImages[idx], data.image);

        try {
          updateHeaderCounts();
          updateOverallProgress();
          await renderReviewWorkspace();
        } catch (renderErr) {
          console.error("UI refresh error following caption update:", renderErr);
        }
      } catch (err) {
        alert("Caption update failed: " + err.message);
      } finally {
        rwSaveCaptionBtn.disabled = false;
      }
    });
  }

  if (rwResolveCaptionBtn) {
    rwResolveCaptionBtn.addEventListener("click", async () => {
      const sorted = getSortedReviewImages();
      const img = sorted[currentReviewIndex];
      if (!img || !currentProject) return;

      rwResolveCaptionBtn.disabled = true;
      try {
        let res;
        try {
          res = await fetch(`/api/projects/${currentProject.project_id}/images/${img.id}/caption`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              resolve_review: true,
              needs_review: false
            })
          });
        } catch (netErr) {
          throw new Error("Network connection error: " + netErr.message);
        }

        if (!res.ok) {
          let errDetail = "Could not resolve caption flag";
          try {
            const errData = await res.json();
            errDetail = errData.detail || errDetail;
          } catch (_) {}
          throw new Error(errDetail);
        }

        const data = await res.json();
        Object.assign(img, data.image);
        const idx = currentImages.findIndex(i => i.id === img.id);
        if (idx >= 0) Object.assign(currentImages[idx], data.image);

        try {
          updateHeaderCounts();
          updateOverallProgress();
          await renderReviewWorkspace();
        } catch (renderErr) {
          console.error("UI refresh error following caption resolution:", renderErr);
        }
      } catch (err) {
        alert("Could not resolve caption review: " + err.message);
      } finally {
        rwResolveCaptionBtn.disabled = false;
      }
    });
  }

  if (rwCleanDraftBtn) {
    rwCleanDraftBtn.addEventListener("click", async () => {
      const sorted = getSortedReviewImages();
      const img = sorted[currentReviewIndex];
      if (!img || !currentProject || !rwDraftTextarea) return;

      const currentText = rwDraftTextarea.value || "";
      try {
        const res = await fetch(`/api/projects/${currentProject.project_id}/images/${img.id}/clean-draft`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: currentText })
        });
        if (!res.ok) {
          const errData = await res.json();
          throw new Error(errData.detail || "Could not clean draft");
        }
        const data = await res.json();
        if (data.is_valid && data.cleaned_text) {
          rwDraftTextarea.value = data.cleaned_text;
          if (rwContaminationAlert) rwContaminationAlert.style.display = "none";
          updateRwWordCount();
        } else {
          alert("Could not reliably clean draft: " + (data.error_message || "Invalid description format"));
        }
      } catch (err) {
        alert("Clean draft failed: " + err.message);
      }
    });
  }

  // --- Working AI Generation Action ---
  async function triggerAltTextGeneration() {
    const sorted = getSortedReviewImages();
    const img = sorted[currentReviewIndex];
    if (!img || !currentProject) return;

    const lang = rwLangSelect ? rwLangSelect.value : "en-GB";

    // Set UI to loading state
    if (rwGenerateDraftBtn) rwGenerateDraftBtn.disabled = true;
    if (rwRegenerateDraftBtn) rwRegenerateDraftBtn.disabled = true;
    if (rwGenSpinner) rwGenSpinner.style.display = "inline";
    if (rwGenBtnText) rwGenBtnText.textContent = "Generating Draft...";
    if (rwErrorAlert) {
      rwErrorAlert.style.display = "none";
      rwErrorAlert.textContent = "";
    }

    try {
      const res = await fetch(`/api/projects/${currentProject.project_id}/images/${img.id}/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ language: lang })
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail?.message || data.detail || "Draft generation failed");
      }

      if (data.status === "failed" || (data.result && data.result.status === "failed")) {
        const errMsg = data.result?.error_message || data.error_message || "Generation failed";
        throw new Error(errMsg);
      }

      img.processing_status = "drafted";
      const idx = currentImages.findIndex(i => i.id === img.id);
      if (idx >= 0) currentImages[idx].processing_status = "drafted";

      await renderReviewWorkspace();
      refreshAiStatus();
    } catch (err) {
      if (rwErrorAlert) {
        rwErrorAlert.textContent = "Generation failed: " + err.message;
        rwErrorAlert.style.display = "block";
      } else {
        alert("Generation failed: " + err.message);
      }
    } finally {
      if (rwGenerateDraftBtn) rwGenerateDraftBtn.disabled = false;
      if (rwRegenerateDraftBtn) rwRegenerateDraftBtn.disabled = false;
      if (rwGenSpinner) rwGenSpinner.style.display = "none";
      const hasDraft = currentReviewImageVersions && currentReviewImageVersions.length > 0;
      if (rwGenBtnText) rwGenBtnText.textContent = hasDraft ? "✨ Regenerate Alt Text" : "✨ Generate Alt Text";
    }
  }

  if (rwGenerateDraftBtn) {
    rwGenerateDraftBtn.addEventListener("click", triggerAltTextGeneration);
  }
  if (rwRegenerateDraftBtn) {
    rwRegenerateDraftBtn.addEventListener("click", triggerAltTextGeneration);
  }

  // --- Working AI Revision Action with Follow-up prompt ---
  if (rwReviseBtn) {
    rwReviseBtn.addEventListener("click", async () => {
      const sorted = getSortedReviewImages();
      const img = sorted[currentReviewIndex];
      if (!img || !currentProject) return;

      const instruction = (rwRevisionInput ? rwRevisionInput.value : "").trim();
      if (!instruction) {
        if (rwErrorAlert) {
          rwErrorAlert.textContent = "Please enter a follow-up instruction / revision prompt before revising.";
          rwErrorAlert.style.display = "block";
        }
        return;
      }

      const lang = rwLangSelect ? rwLangSelect.value : "en-GB";
      rwReviseBtn.disabled = true;
      if (rwReviseSpinner) rwReviseSpinner.style.display = "inline";
      if (rwErrorAlert) rwErrorAlert.style.display = "none";

      try {
        const res = await fetch(`/api/projects/${currentProject.project_id}/images/${img.id}/generate`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            language: lang,
            revision_instruction: instruction
          })
        });

        const data = await res.json();
        if (!res.ok) {
          throw new Error(data.detail?.message || data.detail || "Revision generation failed");
        }

        if (rwRevisionInput) rwRevisionInput.value = "";

        // Reset image status to drafted (requires conscious re-approval!)
        img.processing_status = "drafted";
        const idx = currentImages.findIndex(i => i.id === img.id);
        if (idx >= 0) currentImages[idx].processing_status = "drafted";

        await renderReviewWorkspace();
      } catch (err) {
        if (rwErrorAlert) {
          rwErrorAlert.textContent = "Revision failed: " + err.message;
          rwErrorAlert.style.display = "block";
        } else {
          alert("Revision failed: " + err.message);
        }
      } finally {
        rwReviseBtn.disabled = false;
        if (rwReviseSpinner) rwReviseSpinner.style.display = "none";
      }
    });
  }

  if (rwSaveDraftBtn) {
    rwSaveDraftBtn.addEventListener("click", async () => {
      const sorted = getSortedReviewImages();
      const img = sorted[currentReviewIndex];
      if (!img || !currentProject) return;

      const text = (rwDraftTextarea.value || "").trim();
      if (!text) {
        alert("Please enter alt text before saving draft.");
        return;
      }

      try {
        const res = await fetch(`/api/projects/${currentProject.project_id}/images/${img.id}/draft`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text, language: rwLangSelect ? rwLangSelect.value : "en-GB" })
        });
        if (!res.ok) {
          const errData = await res.json();
          throw new Error(errData.detail || "Could not save draft edit");
        }
        img.processing_status = "drafted";
        const idx = currentImages.findIndex(i => i.id === img.id);
        if (idx >= 0) currentImages[idx].processing_status = "drafted";

        renderReviewWorkspace();
      } catch (err) {
        alert("Save draft failed: " + err.message);
      }
    });
  }

  if (rwApproveBtn) {
    rwApproveBtn.addEventListener("click", async () => {
      const sorted = getSortedReviewImages();
      const img = sorted[currentReviewIndex];
      if (!img || !currentProject) return;

      const text = (rwDraftTextarea.value || "").trim();
      if (!text) {
        alert("Please generate or enter alt text before approving.");
        return;
      }

      rwApproveBtn.disabled = true;
      rwApproveBtn.innerHTML = "<span>⏳ Approving...</span>";

      try {
        const res = await fetch(`/api/projects/${currentProject.project_id}/images/${img.id}/approve`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            reviewer_id: "client_reviewer",
            alt_text: text,
            version_id: rwVersionSelect.value || null
          })
        });

        if (!res.ok) {
          const errData = await res.json();
          throw new Error(errData.detail || "Approval failed");
        }

        img.processing_status = "approved";
        img.needs_caption_review = 0;
        const idx = currentImages.findIndex(i => i.id === img.id);
        if (idx >= 0) {
          currentImages[idx].processing_status = "approved";
          currentImages[idx].needs_caption_review = 0;
        }

        updateOverallProgress();

        const nextUnapprovedIdx = sorted.findIndex((im, i) => i > currentReviewIndex && im.processing_status !== "approved");
        if (nextUnapprovedIdx !== -1) {
          currentReviewIndex = nextUnapprovedIdx;
        } else if (currentReviewIndex < sorted.length - 1) {
          currentReviewIndex++;
        }

        renderReviewWorkspace();
      } catch (err) {
        alert("Could not approve alt text: " + err.message);
      } finally {
        rwApproveBtn.disabled = false;
        rwApproveBtn.innerHTML = "<span>✓ Approve Alt Text</span>";
      }
    });
  }

  // --- Inspection Modal Logic ---
  function openInspectionModal(img) {
    selectedModalImage = img;
    const modalImg = document.getElementById("modalImg");
    const modalTitle = document.getElementById("modalTitle");
    const modalSubtitle = document.getElementById("modalSubtitle");
    const modalCaption = document.getElementById("modalCaption");
    const modalReviewBox = document.getElementById("modalReviewBox");
    const modalReasonsList = document.getElementById("modalReasonsList");
    const metaPage = document.getElementById("metaPage");
    const metaPlacement = document.getElementById("metaPlacement");
    const metaPos = document.getElementById("metaPos");
    const metaBbox = document.getElementById("metaBbox");
    const metaUuid = document.getElementById("metaUuid");
    const candidatesBody = document.getElementById("candidatesBody");

    const modalGroupBox = document.getElementById("modalGroupBox");
    const modalGroupBadge = document.getElementById("modalGroupBadge");
    const modalGroupDetails = document.getElementById("modalGroupDetails");
    const modalConfirmGroupBtn = document.getElementById("modalConfirmGroupBtn");
    const modalRemoveFromGroupBtn = document.getElementById("modalRemoveFromGroupBtn");
    const modalSplitGroupBtn = document.getElementById("modalSplitGroupBtn");

    const modalAltTextDraft = document.getElementById("modalAltTextDraft");
    const altTextStatusBadge = document.getElementById("altTextStatusBadge");
    const altTextMetaWrap = document.getElementById("altTextMetaWrap");
    const metaModelTag = document.getElementById("metaModelTag");
    const metaTokensTag = document.getElementById("metaTokensTag");
    const metaCostTag = document.getElementById("metaCostTag");
    const metaDurationTag = document.getElementById("metaDurationTag");
    const altTextErrorBox = document.getElementById("altTextErrorBox");
    const approvalStatusTag = document.getElementById("approvalStatusTag");
    const approveDraftBtn = document.getElementById("approveDraftBtn");
    const wordCountBadge = document.getElementById("wordCountBadge");
    const wordCountAlert = document.getElementById("wordCountAlert");

    const imgSrc = `/api/projects/${currentProject.project_id}/images/${img.id}/file`;
    if (modalImg) modalImg.src = imgSrc;
    if (modalTitle) modalTitle.textContent = img.suggested_filename;
    if (modalSubtitle) modalSubtitle.textContent = `Page ${img.page_number} · Placement ${img.placement_index} · Format: ${img.image_format.toUpperCase()}`;
    if (modalCaption) modalCaption.textContent = img.original_caption || "[NO CAPTION DETECTED]";

    if (modalReviewBox && modalReasonsList) {
      if (img.needs_caption_review && img.review_reasons && img.review_reasons.length > 0) {
        modalReviewBox.style.display = "block";
        modalReasonsList.innerHTML = img.review_reasons.map(r => `<li>${escapeHtml(r)}</li>`).join("");
      } else {
        modalReviewBox.style.display = "none";
      }
    }

    if (modalGroupBox) {
      if (img.group_id) {
        modalGroupBox.style.display = "block";
        const grp = currentGroups.find(g => g.id === img.group_id);
        const isCrossPage = grp?.is_cross_page;
        modalGroupBadge.className = isCrossPage ? "status-pill review" : "status-pill matched";
        modalGroupBadge.textContent = isCrossPage ? "Cross-Page Candidate" : "Figure Group";

        const panelText = img.panel_label ? `· Panel ${img.panel_label}` : "";
        const grpTitle = grp ? grp.figure_label || "Figure Group" : "Figure Group";
        const confText = grp ? `Confidence: ${Math.round((grp.confidence || 0.8) * 100)}%` : "";
        const evidence = grp && grp.evidence ? `<br><em>Evidence: ${escapeHtml(grp.evidence.join("; "))}</em>` : "";

        modalGroupDetails.innerHTML = `Associated with <strong>${escapeHtml(grpTitle)}</strong> ${panelText} (${confText})${evidence}`;
        modalConfirmGroupBtn.onclick = () => window.confirmGroup(img.group_id);
        modalRemoveFromGroupBtn.onclick = () => window.removeImageFromGroup(img.group_id, img.id);
        modalSplitGroupBtn.onclick = () => window.splitGroup(img.group_id);
      } else {
        modalGroupBox.style.display = "none";
      }
    }

    if (metaPage) metaPage.textContent = img.page_number;
    if (metaPlacement) metaPlacement.textContent = img.placement_index;
    if (metaPos) metaPos.textContent = img.caption_position || "None";
    if (metaBbox) metaBbox.textContent = `(${img.bbox.x0}, ${img.bbox.y0}) → (${img.bbox.x1}, ${img.bbox.y1})`;
    if (metaUuid) metaUuid.textContent = img.id;

    if (candidatesBody) {
      candidatesBody.innerHTML = "";
      const candidates = img.caption_candidates || [];
      if (candidates.length === 0) {
        candidatesBody.innerHTML = `<tr><td colspan="5" style="text-align:center; color: var(--text-muted);">No candidate text blocks detected nearby within tolerance.</td></tr>`;
      } else {
        candidates.forEach((cand) => {
          const isWinner = (img.original_caption && img.original_caption.includes(cand.text));
          const tr = document.createElement("tr");
          if (isWinner) tr.className = "winner-row";
          tr.innerHTML = `
            <td><strong>${cand.position.toUpperCase()}</strong></td>
            <td>${cand.gap} pt</td>
            <td>${cand.score}</td>
            <td>${cand.has_figure_prefix ? '<span style="color:#10b981;">Yes</span>' : 'No'}</td>
            <td title="${escapeHtml(cand.text)}">${escapeHtml(cand.text.substring(0, 75))}${cand.text.length > 75 ? '...' : ''}</td>
          `;
          candidatesBody.appendChild(tr);
        });
      }
    }

    // Reset draft state in modal
    if (altTextErrorBox) { altTextErrorBox.style.display = "none"; altTextErrorBox.textContent = ""; }
    if (modalAltTextDraft) modalAltTextDraft.value = "";
    if (altTextMetaWrap) altTextMetaWrap.style.display = "none";
    if (altTextStatusBadge) { altTextStatusBadge.className = "status-pill review"; altTextStatusBadge.textContent = "Not Drafted"; }
    if (approvalStatusTag) { approvalStatusTag.className = "approval-tag unapproved"; approvalStatusTag.textContent = "Draft Unapproved"; }
    if (approveDraftBtn) approveDraftBtn.disabled = true;

    // Load versions for this image
    fetch(`/api/projects/${currentProject.project_id}/images/${img.id}/versions`)
      .then(res => res.json())
      .then(data => {
        if (data.latest_version) {
          const lv = data.latest_version;
          if (modalAltTextDraft) modalAltTextDraft.value = lv.text || "";
          if (lv.status === "success") {
            if (altTextStatusBadge) { altTextStatusBadge.className = "status-pill matched"; altTextStatusBadge.textContent = `Draft (v${lv.version_number})`; }
            if (altTextMetaWrap) altTextMetaWrap.style.display = "flex";
            if (metaModelTag) metaModelTag.textContent = `Model: ${lv.model || "HF"}`;
            if (metaTokensTag) metaTokensTag.textContent = `Tokens: In ${lv.input_tokens || 0} / Out ${lv.output_tokens || 0}`;
            if (metaCostTag) metaCostTag.textContent = `Est. Cost: $${(lv.estimated_cost_usd || 0).toFixed(5)}`;
            if (metaDurationTag) metaDurationTag.textContent = `Duration: ${(lv.duration_seconds || 0).toFixed(2)}s`;

            if (data.is_approved) {
              if (approvalStatusTag) { approvalStatusTag.className = "approval-tag approved"; approvalStatusTag.textContent = "✓ Approved"; }
              if (approveDraftBtn) approveDraftBtn.disabled = true;
            } else {
              if (approvalStatusTag) { approvalStatusTag.className = "approval-tag unapproved"; approvalStatusTag.textContent = "Draft Unapproved"; }
              if (approveDraftBtn) approveDraftBtn.disabled = false;
            }
          }
        }
      })
      .catch(() => {});

    if (inspectionModal) inspectionModal.classList.add("active");
  }

  if (modalCloseBtn) {
    modalCloseBtn.addEventListener("click", () => {
      if (inspectionModal) inspectionModal.classList.remove("active");
    });
  }

  // --- Render Figure Groups Review Cards ---
  function renderGroups(groups) {
    if (!groupsSection || !groupsGrid) return;
    if (!groups || groups.length === 0) {
      groupsSection.style.display = "none";
      return;
    }

    groupsSection.style.display = "block";
    groupsGrid.innerHTML = "";

    groups.forEach(grp => {
      const card = document.createElement("div");
      const isCrossPage = Boolean(grp.is_cross_page);
      const isConfirmed = grp.review_status === "confirmed";

      card.className = `group-card ${isCrossPage ? "cross-page" : ""} ${isConfirmed ? "confirmed" : ""}`;
      card.dataset.groupId = grp.id;

      const pageLabel = isCrossPage ? `Pages ${grp.pages_spanned || grp.primary_page}` : `Page ${grp.primary_page}`;
      const confPct = Math.round((grp.confidence || 0.8) * 100);
      const memberImages = grp.images || currentImages.filter(img => img.group_id === grp.id);

      const statusBadgeClass = isConfirmed ? "matched" : "review";
      const statusBadgeText = isConfirmed ? "CONFIRMED" : (isCrossPage ? "CROSS-PAGE CANDIDATE" : "CANDIDATE GROUP");

      const evidenceHtml = grp.evidence && grp.evidence.length > 0
        ? `<div class="group-meta-sub">Evidence: ${escapeHtml(grp.evidence.join("; "))}</div>`
        : "";

      let membersHtml = "";
      memberImages.forEach(m => {
        const imgSrc = `/api/projects/${currentProject.project_id}/images/${m.id}/file`;
        const label = m.panel_label ? `Panel ${m.panel_label}` : `Page ${m.page_number}`;
        membersHtml += `
          <div class="group-member-item">
            <img class="group-member-thumb" src="${imgSrc}" alt="${m.suggested_filename}" />
            <span>${label}</span>
            <button class="btn-group-action remove" style="margin-top:4px;" onclick="window.removeImageFromGroup('${grp.id}', '${m.id}')">Remove</button>
          </div>
        `;
      });

      card.innerHTML = `
        <div class="group-card-header">
          <div class="group-title-wrap">
            <div class="group-title">${escapeHtml(grp.figure_label || "Figure Group")}</div>
            <div class="group-meta-sub">${pageLabel} · ${memberImages.length} images · Confidence: ${confPct}%</div>
            ${evidenceHtml}
          </div>
          <div class="group-badges-wrap">
            <span class="status-pill ${statusBadgeClass}">${statusBadgeText}</span>
          </div>
        </div>
        <div class="group-caption-quote" title="${escapeHtml(grp.caption)}">
          "${escapeHtml(grp.caption)}"
        </div>
        <div class="group-members-list">
          ${membersHtml}
        </div>
        <div class="group-actions-footer">
          <button class="btn-group-action confirm" onclick="window.confirmGroup('${grp.id}')">✓ Confirm Group</button>
          <button class="btn-group-action split" onclick="window.splitGroup('${grp.id}')">✂️ Split Group</button>
        </div>
      `;

      groupsGrid.appendChild(card);
    });
  }

  // Window functions for group card actions
  window.confirmGroup = async (groupId) => {
    if (!currentProject) return;
    try {
      const res = await fetch(`/api/projects/${currentProject.project_id}/groups/${groupId}/confirm`, {
        method: "POST"
      });
      if (res.ok) await reloadProjectState();
    } catch (e) {
      alert("Failed to confirm group: " + e.message);
    }
  };

  window.splitGroup = async (groupId) => {
    if (!confirm("Are you sure you want to split this figure group? Each image will be marked for individual review.")) return;
    try {
      const res = await fetch(`/api/projects/${currentProject.project_id}/groups/${groupId}/split`, {
        method: "POST"
      });
      if (res.ok) await reloadProjectState();
    } catch (e) {
      alert("Failed to split group: " + e.message);
    }
  };

  window.removeImageFromGroup = async (groupId, imageId) => {
    try {
      const res = await fetch(`/api/projects/${currentProject.project_id}/groups/${groupId}/remove-image`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ image_id: imageId })
      });
      if (res.ok) await reloadProjectState();
    } catch (e) {
      alert("Failed to remove image from group: " + e.message);
    }
  };

  function escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // Initialize
  loadProject();
});
