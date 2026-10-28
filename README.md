# AltTextPipeline — AI-Assisted Alt-Text Publishing Pipeline

An editorial workspace and pipeline for extracting images and matching captions from digitally generated book PDFs, generating alt text with AI, and managing iterative editorial review through to Word document export.

---

## Quick Start: Extraction Inspector Web Interface

You can visually test extraction and caption matching in your browser before proceeding to AI generation:

### 1. Environment Setup
```bash
# Create virtual environment if not already present
python3 -m venv .venv
source .venv/bin/activate

# Install required dependencies
pip install --upgrade pip
pip install pymupdf pillow pytest fastapi uvicorn python-multipart httpx
```

### 2. Launch the Web Inspector
```bash
# Start local server on port 8000
python run_inspector.py 8000
```
Open your browser at: **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

### 3. Testing with the Sample PDF (`40345.pdf`)
1. On the home page, click **"Use Sample"** in the control panel.
2. The page numbers are pre-populated with representative pages: `1, 4, 33, 46, 77`.
3. Click **"Run Extraction →"**.
4. Extracted images will appear in the gallery grid:
   - Green **`MATCHED`** pills designate clear caption associations.
   - Amber **`NEEDS REVIEW`** pills designate uncaptioned images or competing figure candidates.
5. Click **"Inspect Details"** on any image card to open the modal drawer, displaying:
   - High-resolution image preview.
   - Selected caption text and position (`above` or `below`).
   - Geometric candidates table with priority scores and distance gaps.
   - Exact bounding-box coordinates `(x0, y0) -> (x1, y1)`.
   - Immutable image UUID.

---

## Running the Automated Test Suite

Run the full pytest suite (29 tests covering baseline extraction, modular extraction, storage & db persistence, and web inspector):

```bash
PYTHONPATH=. .venv/bin/pytest -v
```

---

## Project Structure

```
AltTextPipeline/
├── 40345.pdf                          # Digital book PDF sample (257 pages)
├── baseline_extraction.py             # Original baseline Colab script (preserved untouched)
├── run_inspector.py                   # Web launcher script
├── run_phase1_harness.py              # Phase 1 diagnostic runner
├── run_phase2_benchmark.py            # Phase 2 comparative benchmark runner
├── src/
│   ├── extraction/                    # Phase 2 Modular Extraction Engine
│   │   ├── engine.py                  # Chunked extraction orchestrator
│   │   ├── heuristics.py              # Header/footer filter, geometry & caption scoring
│   │   ├── models.py                  # BoundingBox, CaptionCandidate, ExtractedImage
│   │   └── naming.py                  # Filename sanitization & deduplication
│   ├── storage/                       # Phase 3 File Storage
│   │   └── filestore.py               # Isolated per-project temporary file storage
│   ├── db/                            # Phase 3 Persistence Layer
│   │   ├── connection.py              # SQLite WAL mode connection manager
│   │   ├── migrations.py              # Version-controlled schema migrations
│   │   ├── repository.py              # CRUD repository for projects, assets, jobs
│   │   └── schema.sql                 # Portable relational schema
│   └── web/                           # Early Web Inspector Interface
│       ├── app.py                     # FastAPI application
│       └── static/                    # Frontend HTML5, CSS, and JS
│           ├── index.html
│           ├── styles.css
│           └── app.js
└── tests/                             # Automated test suites
    ├── test_baseline_extraction.py    # Baseline comparison tests
    ├── test_modular_extraction.py     # Modular engine unit tests
    ├── test_storage_and_db.py         # Storage, isolation, safeguards & restart tests
    └── test_web_inspector.py          # Web API & upload integration tests
```
