import test from "node:test";
import assert from "node:assert/strict";

function createFakeElement(tagName = "div") {
  const attrs = new Map();
  const el = {
    tagName: tagName.toUpperCase(),
    id: "",
    className: "",
    style: {},
    dataset: {},
    disabled: false,
    checked: false,
    textContent: "",
    _innerHTML: "",
    childrenById: new Map(),
    setAttribute(k, v) { attrs.set(k, String(v)); },
    getAttribute(k) { return attrs.get(k) ?? null; },
    appendChild(child) {
      if (child.id) this.childrenById.set(child.id, child);
      return child;
    },
    querySelector(sel) {
      if (sel.startsWith("#")) {
        const id = sel.slice(1);
        return this.childrenById.get(id) || null;
      }
      return null;
    },
  };

  Object.defineProperty(el, "innerHTML", {
    get() { return el._innerHTML; },
    set(val) {
      el._innerHTML = String(val);
      const idMatches = el._innerHTML.matchAll(/id="([^"]+)"/g);
      for (const m of idMatches) {
        if (!el.childrenById.has(m[1])) {
          el.childrenById.set(m[1], createFakeElement("div"));
          el.childrenById.get(m[1]).id = m[1];
        }
      }
    },
  });

  return el;
}

function setupFakeDom() {
  const body = createFakeElement("body");
  body.contains = (node) => body.childrenById.has(node?.id);
  const listeners = new Map();

  globalThis.document = {
    body,
    createElement: (tag) => createFakeElement(tag),
    addEventListener: (evt, fn) => listeners.set(evt, fn),
    removeEventListener: (evt) => listeners.delete(evt),
  };
  globalThis.window = {};
  return { body };
}

test("cleanup modal Mode A sends mode=extracted and Mode B sends mode=all_source consistently", async () => {
  const { body } = setupFakeDom();
  const requests = [];

  globalThis.fetch = async (url, opts = {}) => {
    requests.push({ url, method: opts.method || "GET", body: opts.body || null });
    if (url.includes("/cleanup/preview")) {
      const parsed = new URL(url, "http://localhost");
      const mode = parsed.searchParams.get("mode");
      if (mode !== "extracted" && mode !== "all_source") {
        return {
          ok: false,
          status: 400,
          async json() { return { detail: `Invalid cleanup mode '${mode}'.` }; },
        };
      }
      return {
        ok: true,
        status: 200,
        async json() {
          return {
            preview: {
              can_cleanup: true,
              target_blob_count: mode === "all_source" ? 3 : 2,
              records_to_delete: { image_assets: 2, job_batches: 1, jobs: 1 },
              preserved_assets: {
                export_storage_keys: ["exports/out.docx"],
                source_storage_key: "source/book.pdf",
                audit_events_count: 1,
                ai_usage_count: 1,
              },
              blocking_reasons: [],
            },
          };
        },
      };
    }

    if (url.includes("/cleanup/execute")) {
      const payload = JSON.parse(opts.body || "{}");
      if (payload.mode !== "extracted" && payload.mode !== "all_source") {
        return {
          ok: false,
          status: 400,
          async json() { return { detail: `Invalid cleanup mode '${payload.mode}'.` }; },
        };
      }
      return {
        ok: true,
        status: 200,
        async json() {
          return {
            status: "ok",
            operation: { id: "op-1", status: "completed", cleanup_mode: payload.mode },
          };
        },
      };
    }

    throw new Error(`Unexpected fetch URL: ${url}`);
  };

  const { openCleanupModal } = await import("../src/web/static/cleanup-modal.js");

  let completedOp = null;
  openCleanupModal({
    projectId: "proj-1",
    projectName: "Test Book",
    onComplete: (op) => { completedOp = op; },
  });

  // Wait for initial Mode A preview
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(requests[0].url, "/api/projects/proj-1/cleanup/preview?mode=extracted");

  const modal = body.childrenById.get("cleanupModalBackdrop");
  const modeBRadio = modal.querySelector("#modeBRadio");
  const modeARadio = modal.querySelector("#modeARadio");
  const confirmBtn = modal.querySelector("#confirmCleanupBtn");
  const deleteList = modal.querySelector("#cleanupDeleteList");

  // Switch to Mode B -> must request mode=all_source and show source PDF deletion
  modeBRadio.onchange();
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(requests[1].url, "/api/projects/proj-1/cleanup/preview?mode=all_source");
  assert.match(deleteList.innerHTML, /1<\/strong> source PDF file/);

  // Execute Mode B -> must POST {"mode":"all_source"}
  await confirmBtn.onclick();
  const execReqB = requests.find((r) => r.url.includes("/cleanup/execute"));
  assert.equal(execReqB.method, "POST");
  assert.deepEqual(JSON.parse(execReqB.body), { mode: "all_source" });
  assert.equal(completedOp.cleanup_mode, "all_source");

  // Switch back to Mode A -> must request mode=extracted and execute mode=extracted
  requests.length = 0;
  modeARadio.onchange();
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(requests[0].url, "/api/projects/proj-1/cleanup/preview?mode=extracted");
  await confirmBtn.onclick();
  const execReqA = requests.find((r) => r.url.includes("/cleanup/execute"));
  assert.deepEqual(JSON.parse(execReqA.body), { mode: "extracted" });
});
