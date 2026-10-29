/**
 * AltTextPipeline - Extraction Inspector Frontend Client (Phase 4.1)
 */

document.addEventListener("DOMContentLoaded", () => {
  // Elements
  const dropzone = document.getElementById("dropzone");
  const fileInput = document.getElementById("pdfFile");
  const selectedFileText = document.getElementById("selectedFileName");
  const pageRangeInput = document.getElementById("pageRange");
  const projectNameInput = document.getElementById("projectName");
  const extractBtn = document.getElementById("startExtractBtn");
  const progressContainer = document.getElementById("progressContainer");
  const progressText = document.getElementById("progressText");
  const useSampleBtn = document.getElementById("useSampleBtn");

  const resultsSection = document.getElementById("resultsSection");
  const emptyState = document.getElementById("emptyState");
  const galleryGrid = document.getElementById("galleryGrid");
  const groupsSection = document.getElementById("groupsSection");
  const groupsGrid = document.getElementById("groupsGrid");

  const totalCountEl = document.getElementById("totalCount");
  const matchedCountEl = document.getElementById("matchedCount");
  const groupCountEl = document.getElementById("groupCount");
  const reviewCountEl = document.getElementById("reviewCount");

  const filterTabs = document.querySelectorAll(".filter-tab");
  const modalBackdrop = document.getElementById("inspectionModal");
  const modalCloseBtn = document.getElementById("modalCloseBtn");

  const aiConfigBadge = document.getElementById("aiConfigBadge");
  const aiSpendBadge = document.getElementById("aiSpendBadge");

  // Extracted-Image Reader elements
  const readerFirstPageBtn = document.getElementById("readerFirstPageBtn");
  const readerPrevPageBtn = document.getElementById("readerPrevPageBtn");
  const readerNextPageBtn = document.getElementById("readerNextPageBtn");
  const readerLastPageBtn = document.getElementById("readerLastPageBtn");
  const readerPageJumpInput = document.getElementById("readerPageJumpInput");
  const readerTotalPagesLabel = document.getElementById("readerTotalPagesLabel");
  const readerPageImageCountBadge = document.getElementById("readerPageImageCountBadge");
  const skipEmptyPagesToggle = document.getElementById("skipEmptyPagesToggle");

  // Modal AI generation elements
  const generateDraftBtn = document.getElementById("generateDraftBtn");
  const genBtnSpinner = document.getElementById("genBtnSpinner");
  const genBtnText = document.getElementById("genBtnText");
  const altTextLangSelect = document.getElementById("altTextLangSelect");
  const customUserPromptInput = document.getElementById("customUserPromptInput");
  const resetPromptBtn = document.getElementById("resetPromptBtn");
  const modalAltTextDraft = document.getElementById("modalAltTextDraft");
  const altTextStatusBadge = document.getElementById("altTextStatusBadge");
  const altTextMetaWrap = document.getElementById("altTextMetaWrap");
  const metaModelTag = document.getElementById("metaModelTag");
  const metaTokensTag = document.getElementById("metaTokensTag");
  const metaCostTag = document.getElementById("metaCostTag");
  const metaDurationTag = document.getElementById("metaDurationTag");
  const altTextErrorBox = document.getElementById("altTextErrorBox");

  // Word count & approval elements
  const wordCountBadge = document.getElementById("wordCountBadge");
  const wordCountAlert = document.getElementById("wordCountAlert");
  const approvalStatusTag = document.getElementById("approvalStatusTag");
  const approveDraftBtn = document.getElementById("approveDraftBtn");

  // Group box elements in modal
  const modalGroupBox = document.getElementById("modalGroupBox");
  const modalGroupBadge = document.getElementById("modalGroupBadge");
  const modalGroupDetails = document.getElementById("modalGroupDetails");
  const modalConfirmGroupBtn = document.getElementById("modalConfirmGroupBtn");
  const modalRemoveFromGroupBtn = document.getElementById("modalRemoveFromGroupBtn");
  const modalSplitGroupBtn = document.getElementById("modalSplitGroupBtn");

  // Step 4: Selection dock and context menu elements
  const selectionDock = document.getElementById("selectionDock");
  const selectionCountLabel = document.getElementById("selectionCountLabel");
  const selectionPagesLabel = document.getElementById("selectionPagesLabel");
  const groupSelectedBtn = document.getElementById("groupSelectedBtn");
  const clearSelectionBtn = document.getElementById("clearSelectionBtn");
  const imageContextMenu = document.getElementById("imageContextMenu");
  const contextMenuGroupBtn = document.getElementById("contextMenuGroupBtn");
  const contextMenuInspectBtn = document.getElementById("contextMenuInspectBtn");

  const selectedImageIds = new Set();
  let isSelectionMode = false;
  let activeContextMenuImage = null;

  // Caption grouping modal elements
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

  // Review mode revision elements
  const rwRevisionInput = document.getElementById("rwRevisionInput");
  const rwReviseBtn = document.getElementById("rwReviseBtn");
  const rwReviseSpinner = document.getElementById("rwReviseSpinner");
  const rwErrorAlert = document.getElementById("rwErrorAlert");

  let currentReviewImageApproval = null;

  let currentProject = null;
  let currentImages = [];
  let currentGroups = [];
  let currentFilter = "all";
  let selectedFile = null;
  let useSamplePdf = false;
  let selectedModalImage = null;
  let aiConfig = null;
  let currentPdfPage = 1;
  let skipEmptyPages = true;

  // Persistent Text Size Control (Phase B)
  const textSizeButtons = document.querySelectorAll(".btn-text-size");
  function applyTextSize(size) {
    document.documentElement.setAttribute("data-text-size", size);
    localStorage.setItem("alttext_text_size", size);
    textSizeButtons.forEach(btn => {
      btn.classList.toggle("active", btn.dataset.size === size);
    });
  }
  const savedSize = localStorage.getItem("alttext_text_size") || "normal";
  applyTextSize(savedSize);

  textSizeButtons.forEach(btn => {
    btn.addEventListener("click", () => {
      applyTextSize(btn.dataset.size);
    });
  });

  // View Switcher Elements (Phase B)
  const workspaceNav = document.getElementById("workspaceNav");
  const viewReviewBtn = document.getElementById("viewReviewBtn");
  const viewReaderBtn = document.getElementById("viewReaderBtn");
  const viewExportBtn = document.getElementById("viewExportBtn");
  const reviewWorkspaceSection = document.getElementById("reviewWorkspaceSection");
  const exportSection = document.getElementById("exportSection");
  const reviewProgressBarText = document.getElementById("reviewProgressBarText");

  let activeView = "review";

  // Review Workspace Elements (Phase B)
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

  const rwVersionBadge = document.getElementById("rwVersionBadge");
  const rwLangSelect = document.getElementById("rwLangSelect");
  const rwDraftTextarea = document.getElementById("rwDraftTextarea");
  const rwWordCountBadge = document.getElementById("rwWordCountBadge");
  const rwVersionSelect = document.getElementById("rwVersionSelect");
  const rwGenerateDraftBtn = document.getElementById("rwGenerateDraftBtn");
  const rwSaveDraftBtn = document.getElementById("rwSaveDraftBtn");
  const rwApproveBtn = document.getElementById("rwApproveBtn");

  // Focused preview lightbox modal elements
  const focusedPreviewModal = document.getElementById("focusedPreviewModal");
  const focusedPreviewTitle = document.getElementById("focusedPreviewTitle");
  const focusedPreviewImg = document.getElementById("focusedPreviewImg");
  const focusedPreviewCaption = document.getElementById("focusedPreviewCaption");
  const focusedPreviewCloseBtn = document.getElementById("focusedPreviewCloseBtn");

  // DOCX Export Elements (Phase C)
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

  let currentReviewIndex = 0;
  let currentReviewImageVersions = [];

  // Load AI configuration and prompt defaults
  async function refreshAiStatus() {
    try {
      const [cfgRes, usageRes] = await Promise.all([
        fetch("/api/ai/config").then(r => r.json()),
        fetch("/api/ai/usage").then(r => r.json()),
      ]);

      aiConfig = cfgRes;
      const modelShort = cfgRes.model ? cfgRes.model.split("/").pop() : "Unknown";
      aiConfigBadge.innerHTML = `<span class="badge-dot" style="background:#10b981;"></span> AI: ${modelShort} (${cfgRes.provider})`;

      const spend = usageRes.tracker ? usageRes.tracker.total_spend_usd.toFixed(4) : "0.0000";
      const maxSpend = usageRes.tracker ? usageRes.tracker.max_monthly_spend_usd.toFixed(2) : "2.00";
      aiSpendBadge.textContent = `Spend: $${spend} / $${maxSpend}`;
    } catch (err) {
      aiConfigBadge.textContent = "AI: Offline";
    }
  }

  refreshAiStatus();

  // Helper: Get default prompt for selected language
  function getDefaultPromptForLang(lang) {
    if (!aiConfig) {
      const langName = lang === "en-US" ? "American English" : "British English";
      return `Based on the provided caption, generate a neutral-toned, item-by-item alt-text description in ${langName} that details all visual elements and text, keeping the total length to 45–50 words.`;
    }
    return lang === "en-US" ? aiConfig.default_prompt_us : aiConfig.default_prompt_gb;
  }

  // Language switch handler: update prompt if untouched or user requests
  altTextLangSelect.addEventListener("change", () => {
    const newLang = altTextLangSelect.value;
    const currentVal = customUserPromptInput.value.trim();
    // If the prompt is empty or equals the previous default, sync it with the new language default
    const prevDefault = newLang === "en-US" ? aiConfig?.default_prompt_gb : aiConfig?.default_prompt_us;
    if (!currentVal || (prevDefault && currentVal === prevDefault)) {
      customUserPromptInput.value = getDefaultPromptForLang(newLang);
    }
  });

  // Explicit Reset Prompt action
  if (resetPromptBtn) {
    resetPromptBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      const lang = altTextLangSelect.value;
      customUserPromptInput.value = getDefaultPromptForLang(lang);
    });
  }

  // Word count validator
  function countWords(text) {
    if (!text) return 0;
    const matches = text.trim().match(/\b[\w'-]+\b/g);
    return matches ? matches.length : 0;
  }

  function updateWordCountValidation() {
    const text = modalAltTextDraft.value;
    const count = countWords(text);

    wordCountBadge.textContent = `${count} words`;

    if (count === 0) {
      wordCountBadge.className = "word-count-badge";
      wordCountAlert.textContent = "";
      return;
    }

    if (count >= 45 && count <= 50) {
      wordCountBadge.className = "word-count-badge valid";
      wordCountAlert.textContent = "✓ Target length (45–50 words)";
      wordCountAlert.style.color = "#34d399";
    } else {
      wordCountBadge.className = "word-count-badge warning";
      const diff = count < 45 ? `${45 - count} under minimum (target 45–50)` : `${count - 50} over maximum (target 45–50)`;
      wordCountAlert.textContent = `⚠️ Length warning: ${diff}`;
      wordCountAlert.style.color = "#fbbf24";
    }
  }

  modalAltTextDraft.addEventListener("input", updateWordCountValidation);

  // Check sample PDF availability
  fetch("/api/sample-pdf-available")
    .then(res => res.json())
    .then(data => {
      if (data.available) {
        document.getElementById("sampleCard").style.display = "flex";
      }
    })
    .catch(() => {});

  // Drag & drop handlers
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
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      handleFileSelected(e.dataTransfer.files[0]);
    }
  });

  fileInput.addEventListener("change", (e) => {
    if (e.target.files && e.target.files[0]) {
      handleFileSelected(e.target.files[0]);
    }
  });

  function handleFileSelected(file) {
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      alert("Please select a valid PDF file.");
      return;
    }
    selectedFile = file;
    useSamplePdf = false;
    selectedFileText.textContent = `Selected: ${file.name} (${(file.size / (1024 * 1024)).toFixed(1)} MB)`;
    extractBtn.disabled = false;
  }

  // Quick pick sample button
  useSampleBtn.addEventListener("click", () => {
    selectedFile = null;
    useSamplePdf = true;
    selectedFileText.textContent = "Selected: 40345.pdf (Local Sample Volume)";
    pageRangeInput.value = "1, 4, 33, 46, 77";
    projectNameInput.value = "Inspection - 40345 Sample Pages";
    extractBtn.disabled = false;
  });

  // Start Extraction
  extractBtn.addEventListener("click", async () => {
    if (!selectedFile && !useSamplePdf) {
      alert("Please select a PDF file first.");
      return;
    }

    const formData = new FormData();
    if (useSamplePdf) {
      formData.append("use_sample_pdf", "true");
    } else {
      formData.append("file", selectedFile);
    }

    const pages = pageRangeInput.value.trim();
    if (pages) {
      formData.append("pages", pages);
    }

    const prjName = projectNameInput.value.trim();
    if (prjName) {
      formData.append("project_name", prjName);
    }

    // UI state: progress running
    extractBtn.disabled = true;
    progressContainer.style.display = "block";
    progressText.textContent = "Processing PDF and extracting image/caption pairs & figure groups...";

    try {
      const response = await fetch("/api/upload-and-extract", {
        method: "POST",
        body: formData
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || "Extraction failed");
      }

      const data = await response.json();
      currentProject = data;
      currentImages = data.images || [];
      currentGroups = data.groups || [];

      renderResults(data);
    } catch (err) {
      alert(`Extraction Error: ${err.message}`);
    } finally {
      extractBtn.disabled = false;
      progressContainer.style.display = "none";
    }
  });

  // Filter Tabs
  filterTabs.forEach(tab => {
    tab.addEventListener("click", () => {
      filterTabs.forEach(t => t.classList.remove("active"));
      tab.classList.add("active");
      currentFilter = tab.dataset.filter;
      renderGallery();
    });
  });

  function renderResults(data) {
    emptyState.style.display = "none";
    if (workspaceNav) workspaceNav.style.display = "flex";
    resultsSection.style.display = "none";

    totalCountEl.textContent = data.stats.total_images;
    matchedCountEl.textContent = data.stats.matched;
    if (groupCountEl) {
      groupCountEl.textContent = data.stats.figure_groups || (data.groups ? data.groups.length : 0);
    }
    reviewCountEl.textContent = data.stats.needs_review;

    // Set initial page to first page with images, or page 1
    const pagesWithImages = getPagesWithImages();
    if (!currentPdfPage || !pagesWithImages.includes(currentPdfPage)) {
      currentPdfPage = pagesWithImages.length > 0 ? pagesWithImages[0] : 1;
    }

    updateReaderControls();
    updateSelectionDock();
    fetchGroupsAndRender();
    renderGallery();

    // Navigate to Extracted-Image Page Reader upon extraction
    currentReviewIndex = 0;
    switchView("reader");
  }

  async function fetchGroupsAndRender() {
    if (!currentProject) return;
    try {
      const res = await fetch(`/api/projects/${currentProject.project_id}/groups`);
      if (res.ok) {
        const data = await res.json();
        currentGroups = data.groups || [];
        renderGroups(currentGroups);
      }
    } catch (e) {
      console.warn("Could not load groups:", e);
    }
  }

  function renderGroups(groups) {
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

  // Global window functions for group card actions
  window.confirmGroup = async (groupId) => {
    if (!currentProject) return;
    try {
      const res = await fetch(`/api/projects/${currentProject.project_id}/groups/${groupId}/confirm`, {
        method: "POST"
      });
      if (res.ok) {
        await reloadProjectState();
      }
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
      if (res.ok) {
        await reloadProjectState();
      }
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
      if (res.ok) {
        await reloadProjectState();
      }
    } catch (e) {
      alert("Failed to remove image from group: " + e.message);
    }
  };

  async function reloadProjectState() {
    if (!currentProject) return;
    const [projRes, imgRes, grpRes] = await Promise.all([
      fetch(`/api/projects/${currentProject.project_id}`).then(r => r.json()),
      fetch(`/api/projects/${currentProject.project_id}/images`).then(r => r.json()),
      fetch(`/api/projects/${currentProject.project_id}/groups`).then(r => r.json()),
    ]);
    currentImages = imgRes.images || [];
    currentGroups = grpRes.groups || [];
    updateReaderControls();
    renderResults(projRes);
  }

  function getPagesWithImages() {
    return [...new Set(currentImages.map(img => img.page_number))].sort((a, b) => a - b);
  }

  function getTotalPdfPages() {
    if (currentProject && currentProject.total_pages) {
      return currentProject.total_pages;
    }
    if (currentProject && currentProject.pages_processed && currentProject.pages_processed.length > 0) {
      return Math.max(...currentProject.pages_processed);
    }
    if (currentImages && currentImages.length > 0) {
      return Math.max(...currentImages.map(i => i.page_number));
    }
    return 1;
  }

  function updateReaderControls() {
    const totalPages = getTotalPdfPages();
    const pagesWithImages = getPagesWithImages();

    if (readerTotalPagesLabel) {
      readerTotalPagesLabel.textContent = `of ${totalPages}`;
    }

    if (readerPageJumpInput) {
      readerPageJumpInput.value = currentPdfPage;
      readerPageJumpInput.min = "1";
      readerPageJumpInput.max = String(totalPages);
    }

    const pageImages = currentImages.filter(img => img.page_number === currentPdfPage);
    if (readerPageImageCountBadge) {
      const count = pageImages.length;
      readerPageImageCountBadge.textContent = `${count} ${count === 1 ? 'image' : 'images'} on page ${currentPdfPage}`;
    }

    if (skipEmptyPagesToggle) {
      skipEmptyPages = skipEmptyPagesToggle.checked;
    }

    if (skipEmptyPages) {
      const hasPrev = pagesWithImages.some(p => p < currentPdfPage);
      const hasNext = pagesWithImages.some(p => p > currentPdfPage);
      if (readerFirstPageBtn) readerFirstPageBtn.disabled = !hasPrev;
      if (readerPrevPageBtn) readerPrevPageBtn.disabled = !hasPrev;
      if (readerNextPageBtn) readerNextPageBtn.disabled = !hasNext;
      if (readerLastPageBtn) readerLastPageBtn.disabled = !hasNext;
    } else {
      if (readerFirstPageBtn) readerFirstPageBtn.disabled = currentPdfPage <= 1;
      if (readerPrevPageBtn) readerPrevPageBtn.disabled = currentPdfPage <= 1;
      if (readerNextPageBtn) readerNextPageBtn.disabled = currentPdfPage >= totalPages;
      if (readerLastPageBtn) readerLastPageBtn.disabled = currentPdfPage >= totalPages;
    }
  }

  function goToPage(pageNum) {
    const totalPages = getTotalPdfPages();
    let target = parseInt(pageNum, 10);
    if (isNaN(target)) target = 1;
    target = Math.max(1, Math.min(target, totalPages));

    currentPdfPage = target;
    updateReaderControls();
    renderGallery();
  }

  window.readerGoToPage = (pageNum) => goToPage(pageNum);

  function navigatePage(direction) {
    const totalPages = getTotalPdfPages();
    const pagesWithImages = getPagesWithImages();

    if (direction === "first") {
      if (skipEmptyPages && pagesWithImages.length > 0) {
        goToPage(pagesWithImages[0]);
      } else {
        goToPage(1);
      }
    } else if (direction === "last") {
      if (skipEmptyPages && pagesWithImages.length > 0) {
        goToPage(pagesWithImages[pagesWithImages.length - 1]);
      } else {
        goToPage(totalPages);
      }
    } else if (direction === "next") {
      if (skipEmptyPages) {
        const nextP = pagesWithImages.find(p => p > currentPdfPage);
        if (nextP !== undefined) goToPage(nextP);
      } else {
        if (currentPdfPage < totalPages) goToPage(currentPdfPage + 1);
      }
    } else if (direction === "prev") {
      if (skipEmptyPages) {
        const prevPages = pagesWithImages.filter(p => p < currentPdfPage);
        if (prevPages.length > 0) goToPage(prevPages[prevPages.length - 1]);
      } else {
        if (currentPdfPage > 1) goToPage(currentPdfPage - 1);
      }
    }
  }

  if (readerFirstPageBtn) readerFirstPageBtn.addEventListener("click", () => navigatePage("first"));
  if (readerPrevPageBtn) readerPrevPageBtn.addEventListener("click", () => navigatePage("prev"));
  if (readerNextPageBtn) readerNextPageBtn.addEventListener("click", () => navigatePage("next"));
  if (readerLastPageBtn) readerLastPageBtn.addEventListener("click", () => navigatePage("last"));

  if (readerPageJumpInput) {
    readerPageJumpInput.addEventListener("change", () => {
      goToPage(readerPageJumpInput.value);
    });
    readerPageJumpInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        goToPage(readerPageJumpInput.value);
      }
    });
  }

  if (skipEmptyPagesToggle) {
    skipEmptyPagesToggle.addEventListener("change", () => {
      skipEmptyPages = skipEmptyPagesToggle.checked;
      updateReaderControls();
      renderGallery();
    });
  }

  // ==========================================================================
  // Step 4: Multi-Selection Mode, Selection Dock & Context Menu
  // ==========================================================================

  function updateSelectionDock() {
    if (!selectionDock) return;
    const count = selectedImageIds.size;
    if (count === 0) {
      selectionDock.style.display = "none";
      isSelectionMode = false;
      return;
    }

    selectionDock.style.display = "block";

    // Selected images across all pages
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
      if (selectedImageIds.size === 0) {
        isSelectionMode = false;
      }
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

  function openContextMenu(img, x, y, triggerEl = null) {
    activeContextMenuImage = img;
    if (!imageContextMenu) return;

    imageContextMenu.style.display = "flex";

    const menuWidth = 200;
    const menuHeight = 90;
    let left = x;
    let top = y;
    if (left + menuWidth > window.innerWidth - 10) {
      left = window.innerWidth - menuWidth - 10;
    }
    if (top + menuHeight > window.innerHeight - 10) {
      top = window.innerHeight - menuHeight - 10;
    }
    if (left < 10) left = 10;
    if (top < 10) top = 10;

    imageContextMenu.style.left = `${left}px`;
    imageContextMenu.style.top = `${top}px`;

    const isSelected = selectedImageIds.has(img.id);
    const groupOptionText = imageContextMenu.querySelector("#contextMenuGroupBtn .context-menu-text");
    if (groupOptionText) {
      groupOptionText.textContent = isSelected ? "Deselect from group" : "Group images with…";
    }

    if (triggerEl) {
      triggerEl.setAttribute("aria-expanded", "true");
    }

    if (contextMenuGroupBtn) {
      contextMenuGroupBtn.focus();
    }
  }

  function closeContextMenu() {
    if (!imageContextMenu) return;
    imageContextMenu.style.display = "none";
    document.querySelectorAll(".card-menu-btn[aria-expanded='true']").forEach(btn => {
      btn.setAttribute("aria-expanded", "false");
    });
    activeContextMenuImage = null;
  }

  if (contextMenuGroupBtn) {
    contextMenuGroupBtn.addEventListener("click", () => {
      if (!activeContextMenuImage) return;
      const img = activeContextMenuImage;
      closeContextMenu();
      toggleSelectImage(img.id);
    });
  }

  if (contextMenuInspectBtn) {
    contextMenuInspectBtn.addEventListener("click", () => {
      if (!activeContextMenuImage) return;
      const img = activeContextMenuImage;
      closeContextMenu();
      openInspectionModal(img);
    });
  }

  if (imageContextMenu) {
    imageContextMenu.addEventListener("keydown", (e) => {
      const items = [contextMenuGroupBtn, contextMenuInspectBtn].filter(Boolean);
      const currentIndex = items.indexOf(document.activeElement);
      if (e.key === "ArrowDown") {
        e.preventDefault();
        const next = (currentIndex + 1) % items.length;
        items[next].focus();
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        const prev = (currentIndex - 1 + items.length) % items.length;
        items[prev].focus();
      }
    });
  }

  document.addEventListener("click", (e) => {
    if (!e.target.closest("#imageContextMenu") && !e.target.closest(".card-menu-btn")) {
      closeContextMenu();
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeContextMenu();
    }
  });

  if (clearSelectionBtn) {
    clearSelectionBtn.addEventListener("click", () => {
      clearSelection();
    });
  }

  if (groupSelectedBtn) {
    groupSelectedBtn.addEventListener("click", () => {
      if (selectedImageIds.size < 2) return;
      openCaptionGroupingModal();
    });
  }

  function openCaptionGroupingModal() {
    if (!currentProject || selectedImageIds.size < 2) return;

    if (groupDecisionError) {
      groupDecisionError.style.display = "none";
      groupDecisionError.textContent = "";
    }

    const selectedImgs = currentImages.filter(img => selectedImageIds.has(img.id));
    if (groupDecisionCount) {
      groupDecisionCount.textContent = selectedImgs.length;
    }

    // Render list of selected images, source pages, and existing captions
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

    // Inspect existing captions
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
        // Case B: Exactly one distinct existing caption and some images have no caption
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

      // Handle radio changes
      const cards = groupDecisionOptions.querySelectorAll(".decision-radio-card");
      cards.forEach(card => {
        card.addEventListener("click", () => {
          cards.forEach(c => c.classList.remove("active"));
          card.classList.add("active");
          const radio = card.querySelector("input[type='radio']");
          if (radio) {
            radio.checked = true;
            currentMode = radio.value;
            if (currentMode === "merged") {
              if (groupFinalCaptionInput) groupFinalCaptionInput.value = mergedProposal;
            } else if (currentMode === "applied_existing") {
              if (groupFinalCaptionInput) groupFinalCaptionInput.value = appliedExistingCaption;
            } else if (currentMode === "custom") {
              if (groupFinalCaptionInput && distinctCaptions.length === 0) {
                groupFinalCaptionInput.value = "";
              }
            }
          }
        });
      });
    }

    if (captionGroupingModal) {
      captionGroupingModal.style.display = "flex";
    }
  }

  function closeCaptionGroupingModal() {
    if (captionGroupingModal) {
      captionGroupingModal.style.display = "none";
    }
    // Cancel leaves all caption associations, groups, and selection unchanged
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

      // Show loading
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

        // Success: clear selection, close modal, reload state
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

  window.selectionState = {
    getSelectedIds: () => Array.from(selectedImageIds),
    toggleSelect: (id) => toggleSelectImage(id),
    clear: () => clearSelection(),
    isSelectionMode: () => isSelectionMode
  };

  function renderGallery() {
    galleryGrid.innerHTML = "";

    // 1. Get images belonging strictly to currentPdfPage
    const pageImages = currentImages.filter(img => img.page_number === currentPdfPage);

    // 2. If no extracted images on currentPdfPage, show clear empty-page state
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

    // 3. Filter current page images according to the active tab filter
    const filtered = pageImages.filter(img => {
      const hasCap = boolCaption(img);
      const isGrouped = Boolean(img.group_id) || img.association_type === "group_matched";
      const isSingle = hasCap && !isGrouped && !img.needs_caption_review;

      if (currentFilter === "single") return isSingle;
      if (currentFilter === "grouped") return isGrouped;
      if (currentFilter === "review") return img.needs_caption_review;
      if (currentFilter === "uncaptioned") return !hasCap;
      return true; // 'all'
    });

    if (filtered.length === 0) {
      galleryGrid.innerHTML = `
        <div class="reader-empty-page" role="status">
          <p class="reader-empty-desc">No images on Page ${currentPdfPage} match the filter "${currentFilter}".</p>
        </div>
      `;
      return;
    }

    // Order consistently within the page by placement index
    filtered.sort((a, b) => (a.placement_index || 0) - (b.placement_index || 0));

    filtered.forEach(img => {
      const card = document.createElement("div");
      card.className = "image-card";
      card.dataset.id = img.id;

      const isSelected = selectedImageIds.has(img.id);
      if (isSelected) {
        card.classList.add("selected");
      }

      const isGrouped = Boolean(img.group_id) || img.association_type === "group_matched";
      const isApproved = img.processing_status === "approved";
      const isMatched = !img.needs_caption_review && boolCaption(img);

      let statusClass = "review";
      let statusLabel = "NEEDS REVIEW";
      if (isApproved) {
        statusClass = "matched";
        statusLabel = "APPROVED";
      } else if (isMatched) {
        statusClass = isGrouped ? "matched" : "matched";
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

      // Keyboard support on card (Shift+F10 or ContextMenu key)
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
          openInspectionModal(img);
        }
      });

      galleryGrid.appendChild(card);
    });
  }

  function boolCaption(img) {
    return Boolean(img.original_caption && img.original_caption.trim().length > 0);
  }

  function openInspectionModal(img) {
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

    const imgSrc = `/api/projects/${currentProject.project_id}/images/${img.id}/file`;
    modalImg.src = imgSrc;
    modalTitle.textContent = img.suggested_filename;
    modalSubtitle.textContent = `Page ${img.page_number} · Placement ${img.placement_index} · Format: ${img.image_format.toUpperCase()}`;

    modalCaption.textContent = img.original_caption || "[NO CAPTION DETECTED]";

    // Review reasons
    if (img.needs_caption_review && img.review_reasons && img.review_reasons.length > 0) {
      modalReviewBox.style.display = "block";
      modalReasonsList.innerHTML = img.review_reasons.map(r => `<li>${escapeHtml(r)}</li>`).join("");
    } else {
      modalReviewBox.style.display = "none";
    }

    // Group box inside modal
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

    metaPage.textContent = img.page_number;
    metaPlacement.textContent = img.placement_index;
    metaPos.textContent = img.caption_position || "None";
    metaBbox.textContent = `(${img.bbox.x0}, ${img.bbox.y0}) → (${img.bbox.x1}, ${img.bbox.y1})`;
    metaUuid.textContent = img.id;

    // Candidates table
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

    selectedModalImage = img;

    // Reset prompt input to language default if empty
    if (!customUserPromptInput.value.trim()) {
      customUserPromptInput.value = getDefaultPromptForLang(altTextLangSelect.value);
    }

    // Reset AI Draft section
    altTextErrorBox.style.display = "none";
    altTextErrorBox.textContent = "";
    modalAltTextDraft.value = "";
    altTextMetaWrap.style.display = "none";
    altTextStatusBadge.className = "status-pill review";
    altTextStatusBadge.textContent = "Not Drafted";
    approvalStatusTag.className = "approval-tag unapproved";
    approvalStatusTag.textContent = "Draft Unapproved";
    approveDraftBtn.disabled = true;

    updateWordCountValidation();

    // Load existing drafts & approval for this image
    fetch(`/api/projects/${currentProject.project_id}/images/${img.id}/versions`)
      .then(res => res.json())
      .then(data => {
        if (data.latest_version) {
          const lv = data.latest_version;
          modalAltTextDraft.value = lv.text || "";
          updateWordCountValidation();

          if (lv.status === "success") {
            altTextStatusBadge.className = "status-pill matched";
            altTextStatusBadge.textContent = `Draft (v${lv.version_number})`;
            altTextMetaWrap.style.display = "flex";
            metaModelTag.textContent = `Model: ${lv.model || "GLM-5.3-Flash"}`;
            metaTokensTag.textContent = `Tokens: In ${lv.input_tokens || 0} / Out ${lv.output_tokens || 0}`;
            metaCostTag.textContent = `Est. Cost: $${(lv.estimated_cost_usd || 0).toFixed(5)}`;
            metaDurationTag.textContent = `Duration: ${(lv.duration_seconds || 0).toFixed(2)}s`;

            if (data.is_approved) {
              approvalStatusTag.className = "approval-tag approved";
              approvalStatusTag.textContent = "✓ Approved";
              approveDraftBtn.disabled = true;
            } else {
              approvalStatusTag.className = "approval-tag unapproved";
              approvalStatusTag.textContent = "Draft Unapproved";
              approveDraftBtn.disabled = false;
            }
          } else {
            altTextStatusBadge.className = "status-pill review";
            altTextStatusBadge.textContent = "Generation Failed";
            altTextErrorBox.style.display = "block";
            altTextErrorBox.textContent = lv.error_message || "Previous generation failed.";
          }
        }
      })
      .catch(() => {});

    modalBackdrop.classList.add("active");
  }

  // Handle Approve button click
  approveDraftBtn.addEventListener("click", async () => {
    if (!currentProject || !selectedModalImage) return;

    try {
      const res = await fetch(`/api/projects/${currentProject.project_id}/images/${selectedModalImage.id}/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reviewer_id: "editor" })
      });

      if (!res.ok) {
        const errData = await res.json();
        throw new Error(errData.detail || "Approval failed");
      }

      approvalStatusTag.className = "approval-tag approved";
      approvalStatusTag.textContent = "✓ Approved";
      approveDraftBtn.disabled = true;
    } catch (err) {
      alert("Could not approve draft: " + err.message);
    }
  });

  // Handle Generate Alt Text button click
  generateDraftBtn.addEventListener("click", async () => {
    if (!currentProject || !selectedModalImage) return;

    const projectId = currentProject.project_id;
    const imageId = selectedModalImage.id;
    const language = altTextLangSelect.value;
    const userPrompt = customUserPromptInput.value.trim() || null;

    // Set UI to loading state
    generateDraftBtn.disabled = true;
    genBtnSpinner.style.display = "inline-block";
    genBtnText.textContent = "Generating Draft...";
    altTextErrorBox.style.display = "none";
    altTextErrorBox.textContent = "";

    try {
      const response = await fetch(`/api/projects/${projectId}/images/${imageId}/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          language: language,
          user_prompt: userPrompt,
        }),
      });

      const data = await response.json();

      if (!response.ok) {
        const errorMsg = data.detail?.message || data.detail || "Generation request failed";
        throw new Error(errorMsg);
      }

      const result = data.result;
      const version = data.version;

      if (result.status === "success") {
        modalAltTextDraft.value = result.draft_text;
        updateWordCountValidation();

        altTextStatusBadge.className = "status-pill matched";
        altTextStatusBadge.textContent = `Draft (v${version.version_number})`;

        approvalStatusTag.className = "approval-tag unapproved";
        approvalStatusTag.textContent = "Draft Unapproved";
        approveDraftBtn.disabled = false;

        altTextMetaWrap.style.display = "flex";
        metaModelTag.textContent = `Model: ${result.model}`;
        const inTok = result.token_usage ? result.token_usage.input_tokens : 0;
        const outTok = result.token_usage ? result.token_usage.output_tokens : 0;
        metaTokensTag.textContent = `Tokens: In ${inTok} / Out ${outTok}`;
        metaCostTag.textContent = `Est. Cost: $${result.estimated_cost_usd.toFixed(5)}`;
        metaDurationTag.textContent = `Duration: ${result.duration_seconds.toFixed(2)}s`;

        refreshAiStatus();
      } else {
        altTextStatusBadge.className = "status-pill review";
        altTextStatusBadge.textContent = "Generation Failed";
        altTextErrorBox.style.display = "block";
        altTextErrorBox.textContent = result.error_message || "Generation error";
      }
    } catch (err) {
      altTextStatusBadge.className = "status-pill review";
      altTextStatusBadge.textContent = "Generation Failed";
      altTextErrorBox.style.display = "block";
      altTextErrorBox.textContent = err.message;
    } finally {
      generateDraftBtn.disabled = false;
      genBtnSpinner.style.display = "none";
      genBtnText.textContent = "Generate Alt Text";
    }
  });

  modalCloseBtn.addEventListener("click", () => {
    modalBackdrop.classList.remove("active");
  });

  modalBackdrop.addEventListener("click", (e) => {
    if (e.target === modalBackdrop) {
      modalBackdrop.classList.remove("active");
    }
  });

  // ============================================================
  // Phase B: Review Workspace & View Navigation Logic
  // ============================================================

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

  function updateOverallProgress() {
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

  function switchView(viewName) {
    activeView = viewName;
    if (viewReviewBtn) viewReviewBtn.classList.toggle("active", viewName === "review");
    if (viewReaderBtn) viewReaderBtn.classList.toggle("active", viewName === "reader");
    if (viewExportBtn) viewExportBtn.classList.toggle("active", viewName === "export");

    if (reviewWorkspaceSection) reviewWorkspaceSection.style.display = viewName === "review" ? "block" : "none";
    if (resultsSection) resultsSection.style.display = viewName === "reader" ? "block" : "none";
    if (exportSection) exportSection.style.display = viewName === "export" ? "block" : "none";

    // URL routing synchronization
    if (currentProject && currentProject.project_id) {
      if (viewName === "review") {
        if (window.location.pathname !== `/review/${currentProject.project_id}`) {
          window.history.pushState(null, "", `/review/${currentProject.project_id}`);
        }
      } else {
        if (window.location.pathname.startsWith("/review/")) {
          window.history.pushState(null, "", `/?project=${currentProject.project_id}`);
        }
      }
    }

    updateOverallProgress();

    if (viewName === "review") {
      renderReviewWorkspace();
    } else if (viewName === "reader") {
      renderGallery();
    } else if (viewName === "export") {
      refreshExportStatus();
    }
  }

  if (viewReviewBtn) viewReviewBtn.addEventListener("click", () => switchView("review"));
  if (viewReaderBtn) viewReaderBtn.addEventListener("click", () => switchView("reader"));
  if (viewExportBtn) viewExportBtn.addEventListener("click", () => switchView("export"));

  async function renderReviewWorkspace() {
    const sorted = getSortedReviewImages();
    if (!sorted || sorted.length === 0) return;

    if (currentReviewIndex < 0) currentReviewIndex = 0;
    if (currentReviewIndex >= sorted.length) currentReviewIndex = sorted.length - 1;

    const img = sorted[currentReviewIndex];
    if (!img) return;

    updateOverallProgress();

    // Header & counters
    const pageNum = img.source_page_number ?? img.page_number ?? 1;
    const cleanFilename = (img.original_filename || "extracted_asset.png").replace(/^.*[\\\/]/, "");
    const baseName = cleanFilename.replace(/\.[^/.]+$/, "");

    if (rwImageHeading) rwImageHeading.textContent = `Image Review — ${baseName}`;
    if (rwPageBadge) rwPageBadge.textContent = `Page ${pageNum}`;
    if (rwFilenameBadge) rwFilenameBadge.textContent = cleanFilename;
    if (rwImageCounter) rwImageCounter.textContent = `${currentReviewIndex + 1} of ${sorted.length}`;

    // Navigation buttons state
    if (rwPrevBtn) rwPrevBtn.disabled = (currentReviewIndex === 0);
    if (rwNextBtn) rwNextBtn.disabled = (currentReviewIndex >= sorted.length - 1);

    // Group badge
    if (rwGroupBadge) {
      if (img.group_id) {
        rwGroupBadge.style.display = "inline-block";
        rwGroupBadge.textContent = img.panel_label ? `Group (${img.panel_label})` : "Grouped Figure";
      } else {
        rwGroupBadge.style.display = "none";
      }
    }

    // Status pill
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

    // Image preview
    const fileUrl = `/api/projects/${currentProject.project_id}/images/${img.id}/file`;
    if (rwImagePreview) {
      rwImagePreview.src = fileUrl;
      rwImagePreview.alt = img.original_caption || `Image extracted from page ${pageNum}`;
    }
    if (rwDimensionsText) {
      rwDimensionsText.textContent = `Page ${pageNum} • ${img.image_format || "PNG"} • ${img.width || "?"}×${img.height || "?"}px`;
    }

    // Review alert box
    const reasons = typeof img.review_reasons === "string" ? JSON.parse(img.review_reasons || "[]") : (img.review_reasons || []);
    if (rwReviewAlertBox && rwReviewReasonsList) {
      if (img.needs_caption_review || reasons.length > 0) {
        rwReviewAlertBox.style.display = "block";
        rwReviewReasonsList.innerHTML = reasons.length > 0
          ? reasons.map(r => `<li>${escapeHtml(r)}</li>`).join("")
          : `<li>Caption association or text requires human verification before final approval.</li>`;
      } else {
        rwReviewAlertBox.style.display = "none";
        rwReviewReasonsList.innerHTML = "";
      }
    }

    // Caption
    const captionText = img.original_caption || "";
    if (rwCaptionText) {
      rwCaptionText.textContent = captionText || "(No caption detected for this image)";
      if (!captionText) {
        rwCaptionBlock.classList.remove("has-caption");
      } else {
        rwCaptionBlock.classList.add("has-caption");
      }
    }
    if (rwCaptionEditInput) {
      rwCaptionEditInput.value = captionText;
    }
    if (rwCaptionExpandBtn) {
      rwCaptionExpandBtn.style.display = captionText.length > 140 ? "inline-block" : "none";
    }

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
      if (rwApproveBtn) {
        rwApproveBtn.innerHTML = "<span>✓ Approve Alt Text</span>";
        rwApproveBtn.className = "btn-approve-primary";
      }
      updateRwWordCount();
      return;
    }

    // Default to latest version so new revisions are immediately shown
    let activeVersion = latestVersion || versions[versions.length - 1];

    versions.forEach(v => {
      const opt = document.createElement("option");
      opt.value = v.id;
      const isApprovedVer = approval && approval.alt_text_version_id === v.id && img && img.processing_status === "approved";
      const flag = isApprovedVer ? " ★ Approved" : "";
      const source = v.provider === "manual" ? "Client Edit" : (v.model ? v.model.split("/").pop() : "AI");
      opt.textContent = `v${v.version_number} (${source})${flag}`;
      if (activeVersion && v.id === activeVersion.id) {
        opt.selected = true;
      }
      rwVersionSelect.appendChild(opt);
    });

    if (activeVersion && rwDraftTextarea) {
      rwDraftTextarea.value = activeVersion.text || "";
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

  function updateRwWordCount() {
    if (!rwDraftTextarea || !rwWordCountBadge) return;
    const text = rwDraftTextarea.value;
    const count = countWords(text);

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

  if (rwDraftTextarea) {
    rwDraftTextarea.addEventListener("input", updateRwWordCount);
  }

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
      if (e.target === focusedPreviewModal) {
        focusedPreviewModal.style.display = "none";
      }
    });
  }

  if (rwSaveCaptionBtn) {
    rwSaveCaptionBtn.addEventListener("click", async () => {
      const sorted = getSortedReviewImages();
      const img = sorted[currentReviewIndex];
      if (!img || !currentProject) return;

      const newCap = (rwCaptionEditInput.value || "").trim();
      try {
        const res = await fetch(`/api/projects/${currentProject.project_id}/images/${img.id}/caption`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ caption: newCap })
        });
        if (!res.ok) {
          const errData = await res.json();
          throw new Error(errData.detail || "Could not update caption");
        }
        const data = await res.json();
        Object.assign(img, data.image);
        const idx = currentImages.findIndex(i => i.id === img.id);
        if (idx >= 0) Object.assign(currentImages[idx], data.image);

        renderReviewWorkspace();
      } catch (err) {
        alert("Caption update failed: " + err.message);
      }
    });
  }

  if (rwGenerateDraftBtn) {
    rwGenerateDraftBtn.addEventListener("click", async () => {
      const sorted = getSortedReviewImages();
      const img = sorted[currentReviewIndex];
      if (!img || !currentProject) return;

      const lang = rwLangSelect ? rwLangSelect.value : "en-GB";
      rwGenerateDraftBtn.disabled = true;
      rwGenerateDraftBtn.innerHTML = "<span>⏳ Generating...</span>";

      if (rwErrorAlert) rwErrorAlert.style.display = "none";

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

        img.processing_status = "drafted";
        const idx = currentImages.findIndex(i => i.id === img.id);
        if (idx >= 0) currentImages[idx].processing_status = "drafted";

        renderReviewWorkspace();
      } catch (err) {
        if (rwErrorAlert) {
          rwErrorAlert.textContent = "Generation failed: " + err.message;
          rwErrorAlert.style.display = "block";
        } else {
          alert("Generation failed: " + err.message);
        }
      } finally {
        rwGenerateDraftBtn.disabled = false;
        rwGenerateDraftBtn.innerHTML = "<span>✨ Regenerate AI Draft</span>";
      }
    });
  }

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

        // Reset image status to drafted (requires explicit re-approval!)
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

        // Update local status
        img.processing_status = "approved";
        img.needs_caption_review = 0;
        const idx = currentImages.findIndex(i => i.id === img.id);
        if (idx >= 0) {
          currentImages[idx].processing_status = "approved";
          currentImages[idx].needs_caption_review = 0;
        }

        updateOverallProgress();

        // Advance to next unapproved image if available, else next index
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

  // ============================================================
  // Phase C: DOCX Export Logic
  // ============================================================

  async function refreshExportStatus() {
    if (!currentProject) return;
    try {
      const res = await fetch(`/api/projects/${currentProject.project_id}/exports/status`);
      if (!res.ok) return;
      const data = await res.json();
      const elig = data.eligibility;

      // Update total images item
      if (checkIconImages && checkTextImages) {
        if (elig.total_images > 0) {
          checkIconImages.textContent = "✅";
          checkTextImages.textContent = `${elig.total_images} extracted images present`;
        } else {
          checkIconImages.textContent = "❌";
          checkTextImages.textContent = "No extracted images in project";
        }
      }

      // Update approvals item
      if (checkIconApprovals && checkTextApprovals) {
        if (elig.unapproved_count === 0 && elig.total_images > 0) {
          checkIconApprovals.textContent = "✅";
          checkTextApprovals.textContent = `All ${elig.total_images} images have approved alt text`;
        } else {
          checkIconApprovals.textContent = "⏳";
          checkTextApprovals.textContent = `${elig.approved_count} of ${elig.total_images} images approved (${elig.unapproved_count} unapproved)`;
        }
      }

      // Update caption reviews item
      if (checkIconReviews && checkTextReviews) {
        if (elig.unresolved_reviews === 0) {
          checkIconReviews.textContent = "✅";
          checkTextReviews.textContent = "No unresolved caption reviews";
        } else {
          checkIconReviews.textContent = "⚠️";
          checkTextReviews.textContent = `${elig.unresolved_reviews} images require caption review`;
        }
      }

      // Blocking alert
      if (exportBlockingAlert && exportBlockingList) {
        if (elig.can_export) {
          exportBlockingAlert.style.display = "none";
          exportBlockingList.innerHTML = "";
        } else {
          exportBlockingAlert.style.display = "block";
          exportBlockingList.innerHTML = elig.blocking_reasons.map(r => `<li>${escapeHtml(r)}</li>`).join("");
        }
      }

      // Export button
      if (startDocxExportBtn) {
        startDocxExportBtn.disabled = !elig.can_export;
      }

      // Render recent exports
      if (exportsHistoryList) {
        const allExports = data.all_exports || [];
        if (allExports.length === 0) {
          exportsHistoryList.innerHTML = `<p class="text-muted">No exports generated for this project yet.</p>`;
        } else {
          exportsHistoryList.innerHTML = allExports.map(exp => `
            <div class="export-history-item" style="display:flex; justify-content:space-between; align-items:center; padding:12px; border:1px solid var(--border-subtle); border-radius:var(--radius-md); margin-bottom:8px; background:var(--bg-input);">
              <div>
                <div style="font-weight:600; color:var(--text-primary); font-size:calc(15px * var(--font-scale));">${escapeHtml(exp.file_storage_key.split("/").pop())}</div>
                <div style="font-size:calc(13px * var(--font-scale)); color:var(--text-muted);">${exp.created_at ? new Date(exp.created_at).toLocaleString() : ""} • Status: <span style="color:#10b981; font-weight:600;">${exp.status}</span></div>
              </div>
              <a href="${exp.download_url}" class="btn-secondary btn-sm" download style="text-decoration:none;">⬇ Download</a>
            </div>
          `).join("");
        }
      }
    } catch (err) {
      console.warn("Could not fetch export status:", err);
    }
  }

  if (startDocxExportBtn) {
    startDocxExportBtn.addEventListener("click", async () => {
      if (!currentProject) return;
      startDocxExportBtn.disabled = true;
      startDocxExportBtn.innerHTML = `<span>Building & Validating DOCX...</span> <span class="spinner" style="display:inline-block; border-color:white; border-top-color:transparent;"></span>`;

      try {
        const res = await fetch(`/api/projects/${currentProject.project_id}/exports/docx`, {
          method: "POST"
        });
        const data = await res.json();
        if (!res.ok) {
          throw new Error(data.detail?.message || data.detail || "DOCX export generation failed");
        }

        // Trigger immediate file download
        if (data.download_url) {
          window.location.href = data.download_url;
        }
        await refreshExportStatus();
      } catch (err) {
        alert("Export Error: " + err.message);
      } finally {
        startDocxExportBtn.disabled = false;
        startDocxExportBtn.innerHTML = `<span>Generate & Download DOCX</span> <span class="btn-icon">⬇</span>`;
      }
    });
  }

  // Keyboard navigation
  document.addEventListener("keydown", (e) => {
    if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) {
      return;
    }
    if (activeView === "review") {
      if (e.key === "ArrowLeft") {
        if (currentReviewIndex > 0) {
          currentReviewIndex--;
          renderReviewWorkspace();
        }
      } else if (e.key === "ArrowRight") {
        const sorted = getSortedReviewImages();
        if (currentReviewIndex < sorted.length - 1) {
          currentReviewIndex++;
          renderReviewWorkspace();
        }
      }
    }
    if (e.key === "Escape" && focusedPreviewModal && focusedPreviewModal.style.display !== "none") {
      focusedPreviewModal.style.display = "none";
    }
  });

  async function loadProjectDirectly(projectId, targetView = "reader") {
    try {
      const [projRes, imgRes, grpRes] = await Promise.all([
        fetch(`/api/projects/${projectId}`),
        fetch(`/api/projects/${projectId}/images`),
        fetch(`/api/projects/${projectId}/groups`),
      ]);
      if (!projRes.ok) {
        console.error("Could not load project:", projectId);
        return;
      }
      const projData = await projRes.json();
      const imgData = imgRes.ok ? await imgRes.json() : { images: [] };
      const grpData = grpRes.ok ? await grpRes.json() : { groups: [] };

      currentProject = {
        project_id: projectId,
        project_name: projData.project_name || projData.project?.name || "Project",
        total_pages: projData.total_pages || 1,
        pages_processed: []
      };
      currentImages = imgData.images || projData.images || [];
      currentGroups = grpData.groups || projData.groups || [];

      if (emptyState) emptyState.style.display = "none";
      if (workspaceNav) workspaceNav.style.display = "flex";

      renderResults(projData);
      switchView(targetView);
    } catch (err) {
      console.error("Error loading project directly:", err);
    }
  }

  async function checkInitialRoute() {
    const path = window.location.pathname;
    if (path.startsWith("/review/")) {
      const parts = path.split("/");
      const pid = parts[2];
      if (pid) {
        await loadProjectDirectly(pid, "review");
        return;
      }
    }

    const params = new URLSearchParams(window.location.search);
    const pid = params.get("project");
    if (pid) {
      const view = params.get("view") || "reader";
      await loadProjectDirectly(pid, view);
    }
  }

  window.addEventListener("popstate", () => {
    checkInitialRoute();
  });

  checkInitialRoute();

  function escapeHtml(str) {
    if (!str) return "";
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }
});
