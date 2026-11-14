import test from "node:test";
import assert from "node:assert/strict";

function createFakeElement(tagName = "div") {
  const attrs = new Map();
  const el = {
    tagName: tagName.toUpperCase(),
    id: "",
    value: "",
    disabled: false,
    style: {},
    textContent: "",
    _innerHTML: "",
    childrenById: new Map(),
    setAttribute(k, v) { attrs.set(k, String(v)); },
    appendChild(child) {
      if (child.id) this.childrenById.set(child.id, child);
      return child;
    },
    querySelector(sel) {
      if (sel.startsWith("#")) return this.childrenById.get(sel.slice(1)) || null;
      return null;
    },
  };
  Object.defineProperty(el, "innerHTML", {
    get() { return el._innerHTML; },
    set(val) {
      el._innerHTML = String(val);
      for (const m of el._innerHTML.matchAll(/id="([^"]+)"/g)) {
        if (!el.childrenById.has(m[1])) {
          const c = createFakeElement("div");
          c.id = m[1];
          el.childrenById.set(m[1], c);
        }
      }
    },
  });
  return el;
}

test("delete project modal requires exact project name and handles partial failure retry", async () => {
  const body = createFakeElement("body");
  body.contains = (node) => body.childrenById.has(node?.id);
  globalThis.document = { body, createElement: (t) => createFakeElement(t) };
  globalThis.window = {};

  let attempt = 0;
  const calls = [];
  globalThis.fetch = async (url, opts = {}) => {
    calls.push({ url, method: opts.method || "GET", body: opts.body || null });
    if (url.endsWith("/delete/preview")) {
      return {
        ok: true,
        status: 200,
        async json() {
          return {
            preview: {
              project_name: "Biology Vol 1",
              can_delete: true,
              target_blob_count: 5,
              records_to_delete: { image_assets: 3, exports: 1, jobs: 1, audit_events: 2 },
              blocking_reasons: [],
            },
          };
        },
      };
    }
    if (url.endsWith("/delete")) {
      attempt += 1;
      return {
        ok: true,
        status: 200,
        async json() {
          return {
            status: "partial_failure",
            operation: { id: "del-op-1", status: "failed", failed_blob_keys: { "exports/out.docx": "503" } },
          };
        },
      };
    }
    if (url.endsWith("/retry")) {
      return {
        ok: true,
        status: 200,
        async json() {
          return {
            status: "deleted",
            operation: { id: "del-op-1", status: "completed", failed_blob_keys: {} },
          };
        },
      };
    }
    throw new Error(`Unexpected URL: ${url}`);
  };

  const { openDeleteProjectModal } = await import("../src/web/static/delete-project-modal.js");
  let completed = false;
  openDeleteProjectModal({
    projectId: "proj-del-ui",
    projectName: "Biology Vol 1",
    onComplete: () => { completed = true; },
  });

  await new Promise((r) => setTimeout(r, 10));
  const modal = body.childrenById.get("deleteProjectModalBackdrop");
  const confirmInput = modal.querySelector("#deleteProjectConfirmInput");
  const confirmBtn = modal.querySelector("#confirmDeleteProjectBtn");
  const retryBtn = modal.querySelector("#deleteProjectRetryBtn");

  // Disabled until exact project name is typed
  assert.equal(confirmBtn.disabled, true);
  confirmInput.value = "Wrong Name";
  confirmInput.oninput();
  assert.equal(confirmBtn.disabled, true);

  confirmInput.value = "Biology Vol 1";
  confirmInput.oninput();
  assert.equal(confirmBtn.disabled, false);

  await confirmBtn.onclick();
  assert.equal(completed, false);
  assert.equal(calls.some((c) => c.url.endsWith("/delete") && JSON.parse(c.body).confirm_project_name === "Biology Vol 1"), true);

  await retryBtn.onclick();
  assert.equal(completed, true);
  assert.equal(calls.some((c) => c.url.endsWith("/api/deletions/del-op-1/retry")), true);
});

test("reopening delete modal with incomplete deletion resumes existing operation without creating another", async () => {
  const body = createFakeElement("body");
  body.contains = (node) => body.childrenById.has(node?.id);
  globalThis.document = { body, createElement: (t) => createFakeElement(t) };
  globalThis.window = {};

  const calls = [];
  globalThis.fetch = async (url, opts = {}) => {
    calls.push({ url, method: opts.method || "GET" });
    if (url.endsWith("/delete/preview")) {
      return {
        ok: true,
        status: 200,
        async json() {
          return {
            preview: {
              project_name: "Crashed Vol",
              can_delete: false,
              can_resume: true,
              is_active_deletion: false,
              target_blob_count: 3,
              records_to_delete: { image_assets: 1, exports: 1, jobs: 1, audit_events: 1 },
              blocking_reasons: ["Project is currently in 'deleting' state."],
              existing_operation: {
                id: "op-crashed-99",
                status: "deleting_blobs",
                error_message: "Worker crashed mid-deletion",
                failed_blob_keys: {},
              },
            },
          };
        },
      };
    }
    if (url.endsWith("/api/deletions/op-crashed-99/retry")) {
      return {
        ok: true,
        status: 200,
        async json() {
          return {
            status: "deleted",
            operation: { id: "op-crashed-99", status: "completed", failed_blob_keys: {} },
          };
        },
      };
    }
    throw new Error(`Unexpected URL: ${url}`);
  };

  const { openDeleteProjectModal } = await import("../src/web/static/delete-project-modal.js");
  let completedOp = null;
  openDeleteProjectModal({
    projectId: "proj-crashed",
    projectName: "Crashed Vol",
    onComplete: (op) => { completedOp = op; },
  });

  await new Promise((r) => setTimeout(r, 10));
  const modal = body.childrenById.get("deleteProjectModalBackdrop");
  const confirmBtn = modal.querySelector("#confirmDeleteProjectBtn");
  const confirmInput = modal.querySelector("#deleteProjectConfirmInput");
  const progressText = modal.querySelector("#deleteProjectProgressText");
  const retryBtn = modal.querySelector("#deleteProjectRetryBtn");

  // Second deletion operation must not be allowed
  assert.equal(confirmBtn.style.display, "none");
  assert.equal(confirmInput.disabled, true);
  assert.match(progressText.textContent, /deleting_blobs/);
  assert.equal(retryBtn.textContent, "Resume Deletion");

  await retryBtn.onclick();
  assert.equal(completedOp?.id, "op-crashed-99");
  assert.equal(calls.some((c) => c.url.endsWith("/delete")), false);
  assert.equal(calls.some((c) => c.url.endsWith("/api/deletions/op-crashed-99/retry")), true);
});
