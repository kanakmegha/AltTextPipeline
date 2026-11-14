/**
 * AltTextPipeline — Word Document Designer (Customize Word Document)
 * 4-step visual designer with live project-data preview, persistent project settings,
 * and one-click DOCX generation & download.
 */

const PRESET_THUMBNAILS = {
  minimal: `
    <div class="preset-thumb" aria-hidden="true">
      <div class="thumb-line title"></div>
      <div class="thumb-img-box"></div>
      <div class="thumb-line"></div>
      <div class="thumb-line" style="width:82%"></div>
    </div>
  `,
  editorial: `
    <div class="preset-thumb" aria-hidden="true">
      <div class="thumb-line accent"></div>
      <div class="thumb-line" style="width:40%"></div>
      <div class="thumb-img-box"></div>
      <div class="thumb-line accent" style="width:48%"></div>
      <div class="thumb-line" style="width:90%"></div>
    </div>
  `,
  table: `
    <div class="preset-thumb" aria-hidden="true">
      <div class="thumb-line title" style="width:50%"></div>
      <div class="thumb-table">
        <div class="thumb-table-cell" style="background:#e2e8f0"></div>
        <div class="thumb-table-cell"></div>
        <div class="thumb-table-cell" style="background:#e2e8f0"></div>
        <div class="thumb-table-cell"></div>
      </div>
    </div>
  `,
  professional: `
    <div class="preset-thumb" aria-hidden="true">
      <div class="thumb-line title" style="width:75%;border-bottom:2px solid #94a3b8"></div>
      <div class="thumb-line" style="width:45%"></div>
      <div class="thumb-img-box"></div>
      <div class="thumb-line" style="width:88%"></div>
    </div>
  `,
};

function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

export function buildPreviewHtml(config, sampleItem, totalImages = 1) {
  const fontSizeMap = { small: "12px", medium: "13.5px", large: "15.5px" };
  const spacingMap = { compact: "8px", normal: "14px", spacious: "22px" };
  const imgWidthMap = { small: "120px", medium: "190px", large: "260px" };

  const fontStyle = `font-family: '${escapeHtml(config.font_family)}', sans-serif; font-size: ${
    fontSizeMap[config.font_size] || "13.5px"
  }; text-align: ${escapeHtml(config.alignment || "left")};`;

  const hStyle = config.heading_style || "plain";
  const formatHeading = (text) => {
    const t = hStyle === "uppercase" ? text.toUpperCase() : text;
    const color = hStyle === "accent" ? "#1e3a8a" : "#0f172a";
    const weight = hStyle === "plain" ? "600" : "700";
    return `<div style="font-weight:${weight}; color:${color}; margin: 6px 0 2px 0;">${escapeHtml(t)}</div>`;
  };

  const labels = config.section_labels || {};
  const enabledFields = (config.fields || []).filter((f) => f.enabled);
  const phrases = config.custom_phrases || [];
  const headerPhrases = phrases
    .filter((p) => p.placement === "header" && p.text)
    .map((p) => `<p style="font-style:italic; color:#475569; margin:4px 0;">${escapeHtml(p.text)}</p>`)
    .join("");
  const beforeAltPhrases = phrases
    .filter((p) => p.placement === "before_alt_text" && p.text)
    .map((p) => `<p style="font-style:italic; color:#475569; margin:4px 0;">${escapeHtml(p.text)}</p>`)
    .join("");
  const afterAltPhrases = phrases
    .filter((p) => p.placement === "after_alt_text" && p.text)
    .map((p) => `<p style="font-style:italic; color:#475569; margin:4px 0;">${escapeHtml(p.text)}</p>`)
    .join("");
  const closingPhrases = phrases
    .filter((p) => p.placement === "closing" && p.text)
    .map((p) => `<p style="font-style:italic; color:#475569; margin:6px 0;">${escapeHtml(p.text)}</p>`)
    .join("");

  const resolveVal = (item, key) => {
    if (!item) return "";
    if (key === "image_id") return item.image_id || "";
    if (key === "filename") return item.suggested_filename || "";
    if (key === "page_number") return item.page_number != null ? String(item.page_number) : "";
    if (key === "figure_label") return item.figure_label || "";
    if (key === "original_caption") return item.original_caption || "";
    if (key === "generated_alt_text") return item.generated_alt_text || "";
    if (key === "approved_alt_text") return item.approved_alt_text || "";
    if (key === "review_status") {
      return item.review_status ? String(item.review_status).replace(/_/g, " ") : "";
    }
    return "";
  };

  let bodyHtml = "";
  if (!sampleItem) {
    bodyHtml = '<p style="color:#64748b; font-style:italic;">No extracted images available in this project yet.</p>';
  } else if (config.style_preset === "table") {
    const showImg = enabledFields.some((f) => f.key === "image");
    const detailHtml = enabledFields
      .filter((f) => f.key !== "image")
      .map((f) => {
        const val = resolveVal(sampleItem, f.key);
        const before = f.key === "approved_alt_text" ? beforeAltPhrases : "";
        const after = f.key === "approved_alt_text" ? afterAltPhrases : "";
        if (!val) return `${before}${after}`;
        const lbl = labels[f.key] ? `<strong>${escapeHtml(labels[f.key])}:</strong> ` : "";
        return `${before}<div style="margin-bottom:4px;">${lbl}${escapeHtml(val)}</div>${after}`;
      })
      .join("");
    const imgCell = showImg
      ? `<td style="width:130px; text-align:center;"><img class="preview-entry-img" style="max-width:110px; margin:0 auto;" src="${escapeHtml(
          sampleItem.image_url || ""
        )}" alt="${escapeHtml(sampleItem.approved_alt_text || "Preview image")}" /></td>`
      : "";
    bodyHtml = `
      <table class="preview-table">
        <thead>
          <tr>${showImg ? "<th>Image</th>" : ""}<th>Image Details &amp; Alt Text</th></tr>
        </thead>
        <tbody>
          <tr>${imgCell}<td>${detailHtml}</td></tr>
        </tbody>
      </table>
    `;
  } else {
    const imgW = imgWidthMap[config.image_size] || "190px";
    bodyHtml = enabledFields
      .map((f) => {
        if (f.key === "image") {
          return `<img class="preview-entry-img" style="max-width:${imgW}; ${
            config.alignment === "center" ? "margin-left:auto;margin-right:auto;" : ""
          }" src="${escapeHtml(sampleItem.image_url || "")}" alt="${escapeHtml(
            sampleItem.approved_alt_text || "Preview image"
          )}" />`;
        }
        const before = f.key === "approved_alt_text" ? beforeAltPhrases : "";
        const after = f.key === "approved_alt_text" ? afterAltPhrases : "";
        const val = resolveVal(sampleItem, f.key);
        if (!val) return `${before}${after}`;
        const lbl = labels[f.key] || "";
        if (["approved_alt_text", "original_caption", "generated_alt_text"].includes(f.key) && lbl) {
          return `${before}${formatHeading(lbl)}<p style="margin:2px 0 6px 0;">${escapeHtml(val)}</p>${after}`;
        }
        return `${before}<p style="margin:2px 0;">${
          lbl ? `<strong>${escapeHtml(lbl)}:</strong> ` : ""
        }${escapeHtml(val)}</p>${after}`;
      })
      .join("");
  }

  const sepNote =
    totalImages > 1
      ? config.entry_separator === "page_break"
        ? '<div style="margin-top:10px; padding:4px 8px; border-top:1px dashed #94a3b8; font-size:11px; color:#64748b; text-align:center;">— Page Break before Entry 2 of ' +
          totalImages +
          " —</div>"
        : config.entry_separator === "line"
          ? `<hr class="preview-divider" style="margin:${spacingMap[config.entry_spacing] || "14px"} 0;" />`
          : `<div style="height:${spacingMap[config.entry_spacing] || "14px"};"></div>`
      : "";

  const titleClass = config.style_preset === "professional" ? "preview-doc-title professional-border" : "preview-doc-title";
  const titleHtml = config.document_title
    ? `<h3 class="${titleClass}" style="color:${hStyle === "accent" ? "#1e3a8a" : "#0f172a"};">${escapeHtml(
        hStyle === "uppercase" ? config.document_title.toUpperCase() : config.document_title
      )}</h3>`
    : "";
  const introHtml = config.introduction
    ? `<p style="margin: 0 0 10px 0;">${escapeHtml(config.introduction)}</p>`
    : "";
  const closingHtml = config.closing_text
    ? `<p style="margin: 12px 0 0 0;">${escapeHtml(config.closing_text)}</p>`
    : "";
  const footerHtml = config.footer_text
    ? `<div class="preview-footer">${escapeHtml(config.footer_text)}</div>`
    : "";

  return `
    <div style="${fontStyle}">
      <div>
        ${titleHtml}
        ${introHtml}
        ${headerPhrases}
        ${bodyHtml}
        ${sepNote}
        ${closingPhrases}
        ${closingHtml}
      </div>
      ${footerHtml}
    </div>
  `;
}

export function initDocDesigner({ projectId = null, onExportComplete = null } = {}) {
  const root = document.getElementById("docDesignerRoot");
  if (!root) return null;

  let state = {
    config: null,
    presets: [],
    availableFields: [],
    sampleItem: null,
    totalImages: 0,
    projects: [],
    previewProjectId: projectId,
    isSaved: false,
    isDirty: false,
  };

  function markDirty() {
    state.isDirty = true;
    updateSaveBadge();
    renderPreview();
  }

  function updateSaveBadge() {
    const badge = document.getElementById("designerSaveBadge");
    if (!badge) return;
    if (state.isDirty) {
      badge.textContent = "Unsaved changes";
      badge.className = "designer-save-badge unsaved";
    } else if (state.isSaved) {
      badge.textContent = "Saved preferences";
      badge.className = "designer-save-badge saved";
    } else {
      badge.textContent = "Default preferences";
      badge.className = "designer-save-badge";
    }
  }

  function renderPresets() {
    const grid = document.getElementById("designerPresetGrid");
    if (!grid || !state.config) return;
    grid.innerHTML = state.presets
      .map((p) => {
        const active = state.config.style_preset === p.id ? "active" : "";
        return `
          <button type="button" class="preset-card ${active}" data-preset="${escapeHtml(p.id)}">
            ${PRESET_THUMBNAILS[p.id] || ""}
            <div class="preset-name">${escapeHtml(p.name)}</div>
            <div class="preset-desc">${escapeHtml(p.description)}</div>
          </button>
        `;
      })
      .join("");

    grid.querySelectorAll(".preset-card").forEach((btn) => {
      btn.onclick = () => {
        const preset = state.presets.find((p) => p.id === btn.dataset.preset);
        if (!preset) return;
        const d = preset.defaults;
        state.config = {
          ...state.config,
          style_preset: d.style_preset,
          font_family: d.font_family,
          font_size: d.font_size,
          heading_style: d.heading_style,
          alignment: d.alignment,
          entry_spacing: d.entry_spacing,
          entry_separator: d.entry_separator,
          image_size: d.image_size,
          fields: d.fields.map((f) => ({ ...f })),
        };
        renderAllControls();
        markDirty();
      };
    });
  }

  function renderFieldsList() {
    const list = document.getElementById("designerFieldsList");
    if (!list || !state.config) return;

    const availMap = new Map(state.availableFields.map((af) => [af.key, af.label]));
    const visibleFields = state.config.fields.filter((f) => availMap.has(f.key));

    list.innerHTML = visibleFields
      .map((f, idx) => {
        const label = availMap.get(f.key) || f.key;
        return `
          <div class="designer-field-row" draggable="true" data-key="${escapeHtml(f.key)}">
            <label class="designer-field-left">
              <span class="designer-drag-handle" aria-hidden="true">⋮⋮</span>
              <input type="checkbox" class="designer-field-check" data-key="${escapeHtml(f.key)}" ${
          f.enabled ? "checked" : ""
        } />
              <span>${escapeHtml(label)}</span>
            </label>
            <div class="designer-field-actions">
              <button type="button" class="btn-move-field btn-move-up" data-key="${escapeHtml(f.key)}" ${
          idx === 0 ? "disabled" : ""
        } aria-label="Move ${escapeHtml(label)} up">↑</button>
              <button type="button" class="btn-move-field btn-move-down" data-key="${escapeHtml(f.key)}" ${
          idx === visibleFields.length - 1 ? "disabled" : ""
        } aria-label="Move ${escapeHtml(label)} down">↓</button>
            </div>
          </div>
        `;
      })
      .join("");

    const moveVisibleField = (key, delta) => {
      const visKeys = visibleFields.map((f) => f.key);
      const pos = visKeys.indexOf(key);
      const targetPos = pos + delta;
      if (pos < 0 || targetPos < 0 || targetPos >= visKeys.length) return;
      const swapKey = visKeys[targetPos];
      const arr = state.config.fields;
      const i1 = arr.findIndex((f) => f.key === key);
      const i2 = arr.findIndex((f) => f.key === swapKey);
      if (i1 >= 0 && i2 >= 0) {
        [arr[i1], arr[i2]] = [arr[i2], arr[i1]];
        renderFieldsList();
        renderSectionLabels();
        markDirty();
      }
    };

    list.querySelectorAll(".designer-field-check").forEach((cb) => {
      cb.onchange = () => {
        const target = state.config.fields.find((f) => f.key === cb.dataset.key);
        if (!target) return;
        const enabledCount = visibleFields.filter((f) => f.enabled).length;
        if (!cb.checked && enabledCount <= 1) {
          cb.checked = true;
          return;
        }
        target.enabled = cb.checked;
        renderSectionLabels();
        markDirty();
      };
    });

    list.querySelectorAll(".btn-move-up").forEach((b) => {
      b.onclick = () => moveVisibleField(b.dataset.key, -1);
    });
    list.querySelectorAll(".btn-move-down").forEach((b) => {
      b.onclick = () => moveVisibleField(b.dataset.key, 1);
    });

    let dragKey = null;
    list.querySelectorAll(".designer-field-row").forEach((row) => {
      row.ondragstart = () => {
        dragKey = row.dataset.key;
        row.classList.add("dragging");
      };
      row.ondragend = () => {
        dragKey = null;
        row.classList.remove("dragging");
      };
      row.ondragover = (e) => e.preventDefault();
      row.ondrop = (e) => {
        e.preventDefault();
        const dropKey = row.dataset.key;
        if (!dragKey || dragKey === dropKey) return;
        const arr = state.config.fields;
        const fromIdx = arr.findIndex((f) => f.key === dragKey);
        const toIdx = arr.findIndex((f) => f.key === dropKey);
        if (fromIdx < 0 || toIdx < 0) return;
        const [moved] = arr.splice(fromIdx, 1);
        arr.splice(toIdx, 0, moved);
        renderFieldsList();
        renderSectionLabels();
        markDirty();
      };
    });
  }

  function renderSectionLabels() {
    const wrap = document.getElementById("designerSectionLabelsGrid");
    if (!wrap || !state.config) return;
    const availMap = new Map(state.availableFields.map((af) => [af.key, af.label]));
    const editable = state.config.fields.filter((f) => f.enabled && f.key !== "image" && availMap.has(f.key));

    wrap.innerHTML = editable
      .map((f) => {
        const val = (state.config.section_labels || {})[f.key] ?? "";
        return `
          <div class="designer-field-group" style="margin-bottom:4px;">
            <label for="lbl_${escapeHtml(f.key)}">${escapeHtml(availMap.get(f.key))} heading</label>
            <input id="lbl_${escapeHtml(f.key)}" class="designer-input designer-label-input" data-key="${escapeHtml(
          f.key
        )}" type="text" maxlength="100" value="${escapeHtml(val)}" placeholder="Optional label" />
          </div>
        `;
      })
      .join("");

    wrap.querySelectorAll(".designer-label-input").forEach((inp) => {
      inp.oninput = () => {
        state.config.section_labels = state.config.section_labels || {};
        state.config.section_labels[inp.dataset.key] = inp.value;
        markDirty();
      };
    });
  }

  function renderCustomPhrases() {
    const list = document.getElementById("designerPhrasesList");
    if (!list || !state.config) return;
    const placementLabels = {
      header: "Top of document",
      before_alt_text: "Before alt text",
      after_alt_text: "After alt text",
      closing: "Closing section",
    };
    list.innerHTML = (state.config.custom_phrases || [])
      .map(
        (ph, idx) => `
        <div class="custom-phrase-row">
          <div>
            <span>${escapeHtml(ph.text)}</span>
            <span style="color:#94a3b8; font-size:11px; margin-left:6px;">(${escapeHtml(
              placementLabels[ph.placement] || ph.placement
            )})</span>
          </div>
          <button type="button" class="btn-move-field btn-remove-phrase" data-idx="${idx}">Remove</button>
        </div>
      `
      )
      .join("");

    list.querySelectorAll(".btn-remove-phrase").forEach((btn) => {
      btn.onclick = () => {
        const idx = Number(btn.dataset.idx);
        state.config.custom_phrases.splice(idx, 1);
        renderCustomPhrases();
        markDirty();
      };
    });
  }

  function bindWordingAndAppearanceInputs() {
    const cfg = state.config;
    if (!cfg) return;

    const bindInput = (id, key) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.value = cfg[key] || "";
      el.oninput = () => {
        cfg[key] = el.value;
        markDirty();
      };
    };

    bindInput("designerDocTitle", "document_title");
    bindInput("designerIntroText", "introduction");
    bindInput("designerFooterText", "footer_text");
    bindInput("designerClosingText", "closing_text");

    const bindSelect = (id, key) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.value = cfg[key];
      el.onchange = () => {
        cfg[key] = el.value;
        markDirty();
      };
    };

    bindSelect("designerFontFamily", "font_family");
    bindSelect("designerFontSize", "font_size");
    bindSelect("designerHeadingStyle", "heading_style");
    bindSelect("designerAlignment", "alignment");
    bindSelect("designerEntrySpacing", "entry_spacing");
    bindSelect("designerEntrySeparator", "entry_separator");
    bindSelect("designerImageSize", "image_size");

    const addPhraseBtn = document.getElementById("designerAddPhraseBtn");
    const phraseTextEl = document.getElementById("designerNewPhraseText");
    const phrasePlaceEl = document.getElementById("designerNewPhrasePlacement");
    if (addPhraseBtn && phraseTextEl && phrasePlaceEl) {
      addPhraseBtn.onclick = () => {
        const txt = (phraseTextEl.value || "").trim();
        if (!txt) return;
        cfg.custom_phrases = cfg.custom_phrases || [];
        if (cfg.custom_phrases.length >= 10) return;
        cfg.custom_phrases.push({ text: txt, placement: phrasePlaceEl.value || "header" });
        phraseTextEl.value = "";
        renderCustomPhrases();
        markDirty();
      };
    }
  }

  function renderPreview() {
    const paper = document.getElementById("docPreviewPaper");
    const pageInfo = document.getElementById("docPreviewPageInfo");
    if (!paper || !state.config) return;
    paper.innerHTML = buildPreviewHtml(state.config, state.sampleItem, state.totalImages);
    if (pageInfo) {
      const total = state.totalImages || 0;
      const pageLabel =
        total > 1 && state.config.entry_separator === "page_break"
          ? `Approx. ${total} page(s) · 1 entry per page`
          : `Showing 1 of ${total} image entry(s)`;
      pageInfo.textContent = pageLabel;
    }
  }

  function renderAllControls() {
    renderPresets();
    renderFieldsList();
    renderSectionLabels();
    renderCustomPhrases();
    bindWordingAndAppearanceInputs();
    updateSaveBadge();
    renderPreview();
  }

  function renderProjectSelector() {
    const bar = document.getElementById("designerPreviewProjectBar");
    const select = document.getElementById("designerPreviewProjectSelect");
    if (!bar || !select) return;
    if (!state.projects || state.projects.length === 0) {
      bar.style.display = "none";
      return;
    }
    bar.style.display = "flex";
    select.innerHTML = state.projects
      .map((p) => `<option value="${escapeHtml(p.id)}" ${p.id === state.previewProjectId ? "selected" : ""}>${escapeHtml(p.name)}</option>`)
      .join("");
    select.onchange = async () => {
      state.previewProjectId = select.value || null;
      await loadPreviewContextOnly();
    };
  }

  async function loadPreviewContextOnly() {
    const qs = state.previewProjectId ? `?project_id=${encodeURIComponent(state.previewProjectId)}` : "";
    const res = await fetch(`/api/settings/doc-design${qs}`, { credentials: "include" });
    if (!res.ok) return;
    const data = await res.json();
    state.availableFields = data.available_fields || [];
    state.sampleItem = data.sample_item || null;
    state.totalImages = data.total_images || 0;
    renderFieldsList();
    renderSectionLabels();
    renderPreview();
  }

  async function loadDesign() {
    const qs = state.previewProjectId ? `?project_id=${encodeURIComponent(state.previewProjectId)}` : "";
    const res = await fetch(`/api/settings/doc-design${qs}`, {
      credentials: "include",
    });
    if (!res.ok) return;
    const data = await res.json();
    state.config = data.design_config;
    state.presets = data.presets || [];
    state.availableFields = data.available_fields || [];
    state.sampleItem = data.sample_item || null;
    state.totalImages = data.total_images || 0;
    state.projects = data.projects || [];
    state.previewProjectId = data.project_id || state.previewProjectId;
    state.isSaved = Boolean(data.is_saved);
    state.isDirty = false;
    renderProjectSelector();
    renderAllControls();
  }

  const saveBtn = document.getElementById("designerSaveBtn");
  if (saveBtn) {
    saveBtn.onclick = async () => {
      if (!state.config) return;
      saveBtn.disabled = true;
      try {
        const res = await fetch("/api/settings/doc-design", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ design_config: state.config }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Failed to save preferences");
        state.config = data.design_config;
        state.isSaved = true;
        state.isDirty = false;
        updateSaveBadge();
      } catch (err) {
        alert(`Could not save Word document preferences: ${err.message}`);
      } finally {
        saveBtn.disabled = false;
      }
    };
  }

  const resetBtn = document.getElementById("designerResetBtn");
  if (resetBtn) {
    resetBtn.onclick = async () => {
      resetBtn.disabled = true;
      try {
        const res = await fetch("/api/settings/doc-design", {
          method: "DELETE",
          credentials: "include",
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Failed to reset preferences");
        state.config = data.design_config;
        state.isSaved = false;
        state.isDirty = false;
        renderAllControls();
      } catch (err) {
        alert(`Could not reset Word document preferences: ${err.message}`);
      } finally {
        resetBtn.disabled = false;
      }
    };
  }

  const generateBtn = document.getElementById("designerGenerateDocxBtn");
  if (generateBtn) {
    generateBtn.onclick = async () => {
      if (!state.config) return;
      generateBtn.disabled = true;
      const origHtml = generateBtn.innerHTML;
      generateBtn.innerHTML = '<span>Generating Word Document...</span> <span class="gen-spinner">⏳</span>';
      try {
        const res = await fetch(`/api/projects/${encodeURIComponent(projectId)}/exports/docx`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ design_config: state.config }),
        });
        const data = await res.json();
        if (!res.ok) {
          throw new Error(data.detail?.message || data.detail || "Export failed");
        }
        const downloadUrl = data.download_url || `/api/projects/${projectId}/exports/${data.export_id}/download`;
        const dlLink = document.createElement("a");
        dlLink.style.display = "none";
        dlLink.href = downloadUrl;
        dlLink.download = data.export?.filename || "Report_alt_text.docx";
        document.body.appendChild(dlLink);
        dlLink.click();
        dlLink.remove();
        if (typeof onExportComplete === "function") await onExportComplete();
      } catch (err) {
        alert(`Export Error: ${err.message}`);
      } finally {
        generateBtn.disabled = false;
        generateBtn.innerHTML = origHtml;
      }
    };
  }

  loadDesign();
  return { getState: () => state, reload: loadDesign };
}
