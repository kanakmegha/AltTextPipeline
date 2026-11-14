/**
 * Settings Page — Word Document Preferences Controller
 */

import { initDocDesigner } from "./doc-designer.js";

function getQueryProjectId() {
  const params = new URLSearchParams(window.location.search || "");
  return (params.get("project_id") || "").trim() || null;
}

function initTextSizeControls() {
  const btns = document.querySelectorAll(".btn-text-size");
  const savedSize = localStorage.getItem("alttext_text_size") || "normal";
  applyTextSize(savedSize, btns);
  btns.forEach((btn) => {
    btn.addEventListener("click", () => {
      const sz = btn.dataset.size || "normal";
      localStorage.setItem("alttext_text_size", sz);
      applyTextSize(sz, btns);
    });
  });
}

function applyTextSize(size, btns) {
  document.documentElement.setAttribute("data-text-size", size);
  btns.forEach((b) => b.classList.toggle("active", b.dataset.size === size));
}

document.addEventListener("DOMContentLoaded", () => {
  initTextSizeControls();
  const projectId = getQueryProjectId();
  const backLink = document.getElementById("settingsBackToExportLink");
  if (backLink && projectId) {
    backLink.href = `/export/${encodeURIComponent(projectId)}`;
    backLink.style.display = "inline-flex";
  }

  initDocDesigner({
    projectId,
  });
});
