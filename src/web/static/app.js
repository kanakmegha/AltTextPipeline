/**
 * AltTextPipeline - Extraction Inspector Frontend Client
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

  const totalCountEl = document.getElementById("totalCount");
  const matchedCountEl = document.getElementById("matchedCount");
  const reviewCountEl = document.getElementById("reviewCount");

  const filterTabs = document.querySelectorAll(".filter-tab");
  const modalBackdrop = document.getElementById("inspectionModal");
  const modalCloseBtn = document.getElementById("modalCloseBtn");

  let currentProject = null;
  let currentImages = [];
  let currentFilter = "all";
  let selectedFile = null;
  let useSamplePdf = false;

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
    progressText.textContent = "Processing PDF and extracting image/caption pairs...";

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
    resultsSection.style.display = "block";

    totalCountEl.textContent = data.stats.total_images;
    matchedCountEl.textContent = data.stats.matched;
    reviewCountEl.textContent = data.stats.needs_review;

    renderGallery();
  }

  function renderGallery() {
    galleryGrid.innerHTML = "";

    const filtered = currentImages.filter(img => {
      if (currentFilter === "matched") return !img.needs_caption_review && boolCaption(img);
      if (currentFilter === "review") return img.needs_caption_review;
      return true;
    });

    if (filtered.length === 0) {
      galleryGrid.innerHTML = `
        <div class="empty-state" style="grid-column: 1 / -1; padding: 40px 20px;">
          <p class="empty-desc">No images match the current filter (${currentFilter}).</p>
        </div>
      `;
      return;
    }

    filtered.forEach(img => {
      const card = document.createElement("div");
      card.className = "image-card";
      card.dataset.id = img.id;

      const isMatched = !img.needs_caption_review && boolCaption(img);
      const statusClass = isMatched ? "matched" : "review";
      const statusLabel = isMatched ? "MATCHED" : "NEEDS REVIEW";

      const imgSrc = `/api/projects/${currentProject.project_id}/images/${img.id}/file`;
      const captionText = img.original_caption || "No caption detected";
      const captionClass = img.original_caption ? "has-caption" : "no-caption";

      card.innerHTML = `
        <div class="card-preview-wrapper">
          <img class="card-img" src="${imgSrc}" alt="${img.suggested_filename}" loading="lazy" />
          <div class="card-badges-overlay">
            <span class="page-badge">PAGE ${img.page_number}</span>
            <span class="status-pill ${statusClass}">${statusLabel}</span>
          </div>
        </div>
        <div class="card-content">
          <h4 class="card-filename" title="${img.suggested_filename}">${img.suggested_filename}</h4>
          <div class="card-caption-block ${captionClass}">
            ${escapeHtml(captionText)}
          </div>
          <div class="card-footer">
            <span>Pos: ${img.caption_position || "None"}</span>
            <button class="view-btn">Inspect Details</button>
          </div>
        </div>
      `;

      card.addEventListener("click", () => openInspectionModal(img));
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

    if (img.needs_caption_review && img.review_reasons && img.review_reasons.length > 0) {
      modalReviewBox.style.display = "block";
      modalReasonsList.innerHTML = img.review_reasons.map(r => `<li>${escapeHtml(r)}</li>`).join("");
    } else {
      modalReviewBox.style.display = "none";
    }

    metaPage.textContent = img.page_number;
    metaPlacement.textContent = img.placement_index;
    metaPos.textContent = img.caption_position || "None";
    metaBbox.textContent = `(${img.bbox.x0}, ${img.bbox.y0}) → (${img.bbox.x1}, ${img.bbox.y1})`;
    metaUuid.textContent = img.id;

    // Render candidates table
    candidatesBody.innerHTML = "";
    const candidates = img.caption_candidates || [];
    if (candidates.length === 0) {
      candidatesBody.innerHTML = `<tr><td colspan="5" style="text-align:center; color: var(--text-muted);">No candidate text blocks detected nearby within tolerance.</td></tr>`;
    } else {
      candidates.forEach((cand, index) => {
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

    modalBackdrop.classList.add("active");
  }

  modalCloseBtn.addEventListener("click", () => {
    modalBackdrop.classList.remove("active");
  });

  modalBackdrop.addEventListener("click", (e) => {
    if (e.target === modalBackdrop) {
      modalBackdrop.classList.remove("active");
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && modalBackdrop.classList.contains("active")) {
      modalBackdrop.classList.remove("active");
    }
  });

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
