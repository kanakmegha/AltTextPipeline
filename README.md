# AltTextPipeline — AI-Assisted Alt-Text Publishing Pipeline

An editorial workspace and pipeline for extracting images and matching captions from digitally generated book PDFs, generating alt text with AI, and managing iterative editorial review through to Word document export.

For production direct-to-Blob uploads and the Render extraction worker, see [the deployment guide](docs/deployment/render-worker.md).

---

## Quick Start: Extraction Inspector & Alt-Text Generation

You can visually inspect image extraction, caption matching, and alt-text generation in your browser:

### 1. Environment Setup
```bash
# Create virtual environment if not already present
python3 -m venv .venv
source .venv/bin/activate

# Install required dependencies
pip install --upgrade pip
pip install pymupdf pillow pytest fastapi uvicorn python-multipart httpx
```

### 2. Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Edit `.env` to configure your Hugging Face credentials and generation settings:
```dotenv
HF_TOKEN=hf_your_fine_grained_token_here
HF_MODEL=zai-org/GLM-5.3-Flash
HF_PROVIDER=novita
HF_ROUTING_POLICY=provider
ALT_TEXT_LANGUAGE=en-GB
AI_REQUEST_TIMEOUT_SECONDS=90
AI_MAX_RETRIES=2
AI_MAX_CONCURRENT_REQUESTS=2
AI_MAX_OUTPUT_TOKENS=500
AI_MAX_MONTHLY_SPEND_USD=2.00
AI_ENABLE_LIVE_TESTS=false
```

#### How to Create a Hugging Face Token:
1. Log in to [Hugging Face](https://huggingface.co).
2. Go to **Settings** → **Access Tokens** → **Create new token**.
3. Choose **Fine-grained**.
4. In permissions, grant **"Make calls to Inference Providers"** (under Inference).
5. Copy the token into your `.env` as `HF_TOKEN`.

> [!IMPORTANT]
> **Data Privacy & Transmission Notice:**
> When cloud inference is enabled, only the specific extracted image bytes, matched caption, and associated text context are transmitted to the external inference provider (Novita via Hugging Face Router). The full source PDF is **never** uploaded. API tokens are kept strictly backend-side and are never exposed to the frontend, browser, or logs.

> [!NOTE]
> **Hugging Face Credits & Spending Guardrails:**
> Hugging Face Pro subscription credits are not unlimited. The application implements an explicit spending cap via `AI_MAX_MONTHLY_SPEND_USD` (default `$2.00`). When cumulative estimated spend reaches this cap, new requests are automatically halted.

---

### 3. Launch the Web Inspector
```bash
python run_inspector.py 8000
```
Open your browser at: **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

- **Offline Mode:** If `HF_TOKEN` is blank or omitted, the application runs entirely offline. The inspector will use a deterministic mock provider for testing UI interactions without consuming network or credits.
- **Live Inference Mode:** When `HF_TOKEN` is present, the inspector routes live requests to `zai-org/GLM-5.3-Flash` on Novita.

---

### 4. Image Inspection & Alt-Text Drafting Workflow
1. Click **"Use Sample"** to load the pre-configured pages `1, 4, 33, 46, 77` from `40345.pdf`.
2. Click **"Run Extraction →"** to extract images and match captions.
3. Click **"Inspect Details"** on any image card to open the modal drawer.
4. In the **"AI Alternative Text Draft"** section:
   - Select output language variant (**British English (en-GB)** or **American English (en-US)**).
   - Optionally enter custom editorial guidance under "Custom Editorial Prompt".
   - Click **"Generate Alt Text"**.
   - Inspect the generated draft in the editable text box.
   - Review audit telemetry: model, tokens, estimated cost, and duration.
   - **Crucial Invariant:** Generated text is **strictly a draft** and is never automatically approved.

---

## Automated Test Suites

AltTextPipeline follows a strict phase-gated testing policy. The default test run is 100% offline-first and consumes zero inference credits:

### Run Offline Tests (Default)
```bash
PYTHONPATH=. .venv/bin/pytest -v
```
*(Runs 45 automated tests covering extraction, geometry heuristics, storage isolation, database migrations, prompt preservation, retry logic, budget limits, and API endpoints. 1 opt-in live test is skipped by default).*

### Run Opt-in Live Inference Test (Single Image)
To test live connectivity and output quality against Novita with a single non-sensitive image:
```bash
AI_ENABLE_LIVE_TESTS=true PYTHONPATH=. .venv/bin/pytest -v -s tests/test_live_generation.py
```

---

## Small Model Evaluation Harness

Benchmark and compare candidate vision models (e.g. `zai-org/GLM-5.3-Flash` vs alternatives) across an evaluation set:

```bash
# Run evaluation on extracted project images (Mock mode)
python evaluate_models.py --project-id <PROJECT_ID> --mock --output data/evaluation_report.json

# Run live evaluation with explicit provider routing
python evaluate_models.py --project-id <PROJECT_ID> --models zai-org/GLM-5.3-Flash --provider novita --language en-GB --limit 15 --output data/evaluation_report.json
```

The evaluation report produces a structured JSON output with the 7-criteria manual review rubric:
1. **Factual correctness**
2. **Visual coverage**
3. **Caption/context relevance**
4. **Hallucination absence**
5. **Accessibility & clarity**
6. **Conciseness**
7. **Publication suitability**

---

## Project Structure

```
AltTextPipeline/
├── 40345.pdf                          # Digital book PDF sample (257 pages)
├── baseline_extraction.py             # Original baseline Colab script (preserved untouched)
├── evaluate_models.py                 # Phase 4 Model Evaluation Harness
├── run_inspector.py                   # Web launcher script
├── run_phase1_harness.py              # Phase 1 diagnostic runner
├── run_phase2_benchmark.py            # Phase 2 comparative benchmark runner
├── src/
│   ├── ai/                            # Phase 4 AI Generation Layer
│   │   ├── config.py                  # Environment config & secret masking
│   │   ├── generator.py               # AltTextGenerator coordinator & draft persistence
│   │   ├── guardrails.py              # BudgetTracker & ConcurrencyLimiter
│   │   ├── models.py                  # GenerationRequest, GenerationResult, TokenUsage
│   │   ├── prompts.py                 # PromptAssembler, grounding & anti-hallucination
│   │   └── providers.py               # Hugging Face (Novita) & Mock AI providers
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
│   │   ├── repository.py              # CRUD repository for projects, assets, jobs, ledger
│   │   └── schema.sql                 # Portable relational schema
│   └── web/                           # Extraction Inspector Web Interface
│       ├── app.py                     # FastAPI application & AI endpoints
│       └── static/                    # Frontend HTML5, CSS, and JS
│           ├── index.html
│           ├── styles.css
│           └── app.js
└── tests/                             # Automated test suites
    ├── test_ai_generation.py          # Phase 4 unit, mock & guardrail tests
    ├── test_baseline_extraction.py    # Baseline comparison tests
    ├── test_live_generation.py        # Opt-in live inference test
    ├── test_modular_extraction.py     # Modular engine unit tests
    ├── test_storage_and_db.py         # Storage, isolation, safeguards & restart tests
    └── test_web_inspector.py          # Web API & upload integration tests
```
