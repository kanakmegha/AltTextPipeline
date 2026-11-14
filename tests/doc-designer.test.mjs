import test from "node:test";
import assert from "node:assert/strict";
import { buildPreviewHtml } from "../src/web/static/doc-designer.js";

test("buildPreviewHtml updates live for presets, field order, custom wording, and footer", () => {
  const sampleItem = {
    image_id: "img-101",
    suggested_filename: "fig1.png",
    page_number: 3,
    figure_label: "Figure 3.1",
    original_caption: "Original figure caption text.",
    generated_alt_text: "Generated draft.",
    approved_alt_text: "Exact approved alt text.",
    review_status: "approved",
    image_url: "/api/projects/p1/images/img-101/file",
  };

  const config = {
    style_preset: "editorial",
    document_title: "Accessibility Review",
    introduction: "Review introduction.",
    footer_text: "Internal Publishing Document",
    closing_text: "Closing summary.",
    font_family: "Georgia",
    font_size: "large",
    heading_style: "uppercase",
    alignment: "left",
    entry_spacing: "spacious",
    entry_separator: "page_break",
    image_size: "medium",
    section_labels: {
      approved_alt_text: "Alternative Text",
      original_caption: "Original Figure Caption",
      figure_label: "Figure",
    },
    custom_phrases: [
      { text: "Prepared for Editorial Review", placement: "header" },
      { text: "Reviewer Check:", placement: "before_alt_text" },
    ],
    fields: [
      { key: "figure_label", enabled: true },
      { key: "image", enabled: true },
      { key: "approved_alt_text", enabled: true },
      { key: "original_caption", enabled: true },
      { key: "filename", enabled: false },
    ],
  };

  const html = buildPreviewHtml(config, sampleItem, 3);
  assert.match(html, /ACCESSIBILITY REVIEW/);
  assert.match(html, /Prepared for Editorial Review/);
  assert.match(html, /ALTERNATIVE TEXT/);
  assert.match(html, /Exact approved alt text\./);
  assert.match(html, /Internal Publishing Document/);
  assert.match(html, /Page Break before Entry 2 of 3/);
  assert.equal(html.includes("fig1.png"), false);

  // Switching to table preset renders a table layout
  config.style_preset = "table";
  const tableHtml = buildPreviewHtml(config, sampleItem, 3);
  assert.match(tableHtml, /<table class="preview-table">/);
});
