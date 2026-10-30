"""
Extraction Inspector - FastAPI Application
------------------------------------------
Local web interface allowing editors and engineers to upload PDFs, run the
Phase 2 modular extraction engine, view extracted assets and captions, and inspect
geometric candidates and review flags.

Production deployment on Vercel uses PostgreSQL + Vercel Blob storage via the
connection/storage factories. Local development uses SQLite + local disk via
run_inspector.py.
"""

import json
import logging
import os
import uuid
import secrets
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("alttext.web")

from src.db.connection_factory import create_connection
from src.storage.storage_factory import create_storage
from src.db.migrations import apply_migrations as sqlite_apply_migrations
from src.db.postgres_migrations import apply_migrations as postgres_apply_migrations
from src.db.repository import Repository
from src.db.postgres_repository import PostgresRepository
from src.storage.filestore import FileStore
from src.storage.base_storage import BaseStorage
from src.extraction.engine import ExtractionEngine
from src.ai.config import AIConfig
from src.ai.providers import AIProvider, HuggingFaceInferenceProvider, MockAIProvider
from src.ai.guardrails import BudgetTracker, ConcurrencyLimiter, BudgetExceededError
from src.ai.generator import AltTextGenerator
from src.ai.prompts import (
    DEFAULT_PROMPT_TEMPLATE,
    resolve_default_prompt,
    resolve_language_name,
)
from src.ai.cleaner import clean_alt_text_response, is_contaminated_alt_text
from src.export import check_export_eligibility, export_project_docx, DocxBuilder, ExportValidationError
from src.web import auth


BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
STATIC_DIR = BASE_DIR / "src" / "web" / "static"
DEFAULT_DB_PATH = str(BASE_DIR / "data" / "pipeline.db")
DEFAULT_STORAGE_DIR = str(BASE_DIR / "data" / "storage")


class GenerateRequestPayload(BaseModel):
    user_prompt: Optional[str] = None
    language: Optional[str] = None
    revision_instruction: Optional[str] = None


class GroupImageActionPayload(BaseModel):
    image_id: str
    panel_label: Optional[str] = None


class CreateGroupFromSelectionPayload(BaseModel):
    image_ids: List[str]
    caption: str
    decision_mode: str
    figure_label: Optional[str] = None
    caption_position: Optional[str] = "below"
    allow_reassignment: Optional[bool] = False


class ImageAssociationUpdatePayload(BaseModel):
    group_id: Optional[str] = None
    caption: Optional[str] = None
    caption_position: Optional[str] = None
    panel_label: Optional[str] = None
    association_type: Optional[str] = None
    needs_review: Optional[bool] = None


class CaptionUpdatePayload(BaseModel):
    caption: Optional[str] = None
    needs_caption_review: Optional[bool] = None
    resolve_review: Optional[bool] = False


class DraftSavePayload(BaseModel):
    text: str
    language: Optional[str] = "en-GB"


class ApproveRequestPayload(BaseModel):
    version_id: Optional[str] = None
    reviewer_id: Optional[str] = "editor"
    alt_text: Optional[str] = None


class LoginPayload(BaseModel):
    email: str
    password: str


def create_app(
    db_conn=None,
    storage=None,
    ai_provider: Optional[AIProvider] = None,
    ai_config: Optional[AIConfig] = None,
    # Backward-compatible parameters for existing tests and local dev
    db_path: Optional[str] = None,
    storage_dir: Optional[str] = None,
) -> FastAPI:
    """
    Creates and configures the FastAPI application.

    Parameters
    ----------
    db_conn : DatabaseConnection | PostgresConnection, optional
        Database connection. If None, one is created from DATABASE_URL (Postgres)
        or DB_PATH (SQLite) environment variables, or from db_path.
    storage : BaseStorage | FileStore, optional
        Storage backend. If None, one is created from BLOB_READ_WRITE_TOKEN
        (Vercel Blob) or STORAGE_DIR (local) environment variables, or from storage_dir.
    db_path : str, optional
        Path to SQLite database (backward compat, local dev only).
    storage_dir : str, optional
        Path to local storage directory (backward compat, local dev only).
    """
    app = FastAPI(
        title="AltTextPipeline - Extraction Inspector",
        description="Local web interface to inspect image extraction, caption matching, and alt-text generation",
        version="0.4.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------
    # Database & storage initialization
    # ------------------------------------------------------------------
    if db_conn is None:
        backend = "postgres" if os.environ.get("DATABASE_URL") else "sqlite"
        if backend == "postgres":
            db_conn = create_connection(backend="postgres")
        else:
            # Backward compat: use db_path if provided
            effective_db_path = db_path or os.environ.get("DB_PATH", DEFAULT_DB_PATH)
            db_conn = create_connection(backend="sqlite", db_path=effective_db_path)

    if storage is None:
        # Backward compat: use storage_dir if provided
        effective_storage_dir = storage_dir or os.environ.get("STORAGE_DIR", DEFAULT_STORAGE_DIR)
        storage = create_storage(backend="local", base_dir=effective_storage_dir)

    # Run migrations appropriate to the backend
    if type(db_conn).__name__ == "PostgresConnection":
        postgres_apply_migrations(db_conn)
        if auth.AUTH_ENABLED:
            auth.ensure_auth_schema(db_conn)
        repo = PostgresRepository(db_conn)
    else:
        sqlite_apply_migrations(db_conn)
        if auth.AUTH_ENABLED:
            auth.ensure_auth_schema(db_conn)
        repo = Repository(db_conn)

    filestore = storage

    engine = ExtractionEngine()

    cfg = ai_config or AIConfig.from_env()

    # Determine AI provider: injected provider -> Hugging Face (if configured) -> Mock provider fallback
    if ai_provider is not None:
        provider = ai_provider
    elif cfg.is_configured():
        provider = HuggingFaceInferenceProvider(cfg)
    else:
        provider = MockAIProvider(provider_name=cfg.hf_provider, model_name=cfg.hf_model)

    budget_tracker = BudgetTracker(max_monthly_spend_usd=cfg.max_monthly_spend_usd)
    total_db_usage = repo.get_total_ai_usage()
    if total_db_usage and total_db_usage.get("total_cost_usd"):
        budget_tracker.total_spend_usd = total_db_usage["total_cost_usd"]
        budget_tracker.total_input_tokens = total_db_usage["total_input_tokens"]
        budget_tracker.total_output_tokens = total_db_usage["total_output_tokens"]
        budget_tracker.request_count = total_db_usage["request_count"]

    concurrency_limiter = ConcurrencyLimiter(cfg.max_concurrent_requests)
    generator = AltTextGenerator(
        repository=repo,
        file_store=filestore,
        provider=provider,
        config=cfg,
        budget_tracker=budget_tracker,
        concurrency_limiter=concurrency_limiter,
    )

    app.state.repo = repo
    app.state.filestore = filestore
    app.state.engine = engine
    app.state.ai_config = cfg
    app.state.ai_provider = provider
    app.state.budget_tracker = budget_tracker
    app.state.generator = generator
    app.state.storage = storage

    # Mount static assets
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # ------------------------------------------------------------------
    # Authentication middleware
    # ------------------------------------------------------------------
    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        request.state.repo = repo

        if auth.AUTH_ENABLED and not auth._is_public_path(request.url.path):
            if request.url.path.startswith("/api/"):
                try:
                    user = auth.require_auth(request)
                    request.state.user = user
                except HTTPException as exc:
                    return JSONResponse(
                        status_code=exc.status_code,
                        content={"detail": exc.detail},
                        headers=exc.headers or {},
                    )
            else:
                request.state.user = None
        else:
            request.state.user = (
                auth.require_auth(request)
                if auth.AUTH_ENABLED
                else {"id": "dev", "email": "dev@local", "is_active": True}
            )

        response = await call_next(request)
        return response

    # ------------------------------------------------------------------
    # Auth endpoints
    # ------------------------------------------------------------------
    @app.post("/api/auth/login")
    async def login(payload: LoginPayload):
        if not auth.AUTH_ENABLED:
            return JSONResponse({"detail": "Authentication is disabled"}, status_code=400)

        user = repo.verify_user_password(payload.email, payload.password)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid email or password")

        token = auth._make_session_token()
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=auth.SESSION_COOKIE_MAX_AGE)).isoformat()
        session_id = str(uuid.uuid4())
        repo.create_session(session_id, user["id"], token, expires_at)

        signed_token = auth._sign(token)
        csrf_token = secrets.token_urlsafe(16)

        response = JSONResponse(
            {"status": "logged_in", "user": {"id": user["id"], "email": user["email"]}}
        )
        response.set_cookie(
            key=auth.SESSION_COOKIE_NAME,
            value=signed_token,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=auth.SESSION_COOKIE_MAX_AGE,
            path="/",
        )
        response.set_cookie(
            key=auth.CSRF_TOKEN_NAME,
            value=csrf_token,
            httponly=False,
            secure=True,
            samesite="lax",
            max_age=auth.SESSION_COOKIE_MAX_AGE,
            path="/",
        )
        return response

    @app.post("/api/auth/logout")
    async def logout(request: Request):
        if not auth.AUTH_ENABLED:
            return JSONResponse({"detail": "Authentication is disabled"}, status_code=400)
        token = auth.get_session_cookie(request)
        if token:
            repo.delete_session(token)
        response = JSONResponse({"status": "logged_out"})
        response.delete_cookie(key=auth.SESSION_COOKIE_NAME, path="/")
        response.delete_cookie(key=auth.CSRF_TOKEN_NAME, path="/")
        return response

    @app.get("/api/auth/session")
    async def get_session(request: Request):
        if not auth.AUTH_ENABLED:
            return JSONResponse({"auth_enabled": False, "user": {"id": "dev", "email": "dev@local"}})
        try:
            user = auth.require_auth(request)
            return JSONResponse({"auth_enabled": True, "user": {"id": user["id"], "email": user["email"]}})
        except HTTPException:
            return JSONResponse({"auth_enabled": True, "user": None}, status_code=401)

    @app.get("/api/auth/csrf-token")
    async def get_csrf_token(request: Request):
        if not auth.AUTH_ENABLED:
            return JSONResponse({"detail": "Authentication is disabled"}, status_code=400)
        existing = request.cookies.get(auth.CSRF_TOKEN_NAME)
        if not existing:
            existing = secrets.token_urlsafe(16)
        return JSONResponse({"csrf_token": existing})

    # ------------------------------------------------------------------
    # Existing routes
    # ------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def index_page():
        index_file = STATIC_DIR / "index.html"
        if not index_file.exists():
            return HTMLResponse("<h2>Upload & Extract frontend static files missing.</h2>", status_code=404)
        return HTMLResponse(index_file.read_text(encoding="utf-8"))

    @app.get("/review/{project_id}", response_class=HTMLResponse)
    async def review_mode_page(project_id: str):
        project = repo.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
        review_file = STATIC_DIR / "review.html"
        if not review_file.exists():
            return HTMLResponse("<h2>Review Mode frontend static files missing.</h2>", status_code=404)
        content = review_file.read_text(encoding="utf-8")
        content = content.replace("{{project_id}}", project_id).replace("{{project_name}}", project.get("name") or "Project Review")
        return HTMLResponse(content)

    @app.get("/export/{project_id}", response_class=HTMLResponse)
    async def export_page(project_id: str):
        project = repo.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
        export_file = STATIC_DIR / "export.html"
        if not export_file.exists():
            return HTMLResponse("<h2>DOCX Export frontend static files missing.</h2>", status_code=404)
        content = export_file.read_text(encoding="utf-8")
        content = content.replace("{{project_id}}", project_id).replace("{{project_name}}", project.get("name") or "Project Export")
        return HTMLResponse(content)

    @app.get("/api/health")
    async def health_check():
        return {"status": "ok", "app": "Extraction Inspector", "version": "0.4.0"}

    @app.get("/api/sample-pdf-available")
    async def sample_pdf_available():
        sample_path = BASE_DIR / "40345.pdf"
        exists = sample_path.exists()
        return {
            "available": exists,
            "filename": "40345.pdf",
            "size_bytes": sample_path.stat().st_size if exists else 0,
            "suggested_sample_pages": "1, 4, 33, 46, 77"
        }

    @app.get("/api/projects")
    async def list_projects():
        projects = repo.list_projects()
        return {"projects": projects}

    @app.get("/api/projects/{project_id}")
    async def get_project(project_id: str):
        project = repo.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        images = repo.list_project_images(project_id)
        groups = repo.list_project_groups(project_id)
        total_pages = max([img["page_number"] for img in images], default=1)
        return {
            "project": project,
            "project_id": project_id,
            "project_name": project["name"],
            "images": images,
            "groups": groups,
            "total_pages": total_pages,
            "stats": {
                "total_images": len(images),
                "captions_found": sum(1 for img in images if bool(img["original_caption"])),
                "needs_review": sum(1 for img in images if img["needs_caption_review"]),
                "matched": sum(1 for img in images if bool(img["original_caption"]) and not img["needs_caption_review"]),
                "group_matched": sum(1 for img in images if img.get("association_type") == "group_matched"),
                "figure_groups": len(groups),
                "cross_page_groups": sum(1 for g in groups if g["is_cross_page"]),
            }
        }

    @app.get("/api/projects/{project_id}/images")
    async def list_project_images(project_id: str):
        project = repo.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        images = repo.list_project_images(project_id)
        return {"images": images}

    @app.get("/api/projects/{project_id}/images/{image_id}/file")
    @app.head("/api/projects/{project_id}/images/{image_id}/file")
    async def get_image_file(project_id: str, image_id: str):
        asset = repo.get_image_asset(image_id)
        if not asset or asset["project_id"] != project_id:
            raise HTTPException(status_code=404, detail="Image asset not found")

        try:
            data = filestore.read_bytes(project_id, asset["file_storage_key"])
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Image binary not found in storage")
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

        media_type = f"image/{asset['image_format']}" if asset["image_format"] != "jpg" else "image/jpeg"
        return Response(
            content=data,
            media_type=media_type,
            headers={"Content-Disposition": f'inline; filename="{asset["suggested_filename"]}"'}
        )

    @app.post("/api/upload-and-extract")
    async def upload_and_extract(
        file: Optional[UploadFile] = File(None),
        use_sample_pdf: bool = Form(False),
        pages: Optional[str] = Form(None),
        project_name: Optional[str] = Form(None),
    ):
        """
        Uploads a PDF (or uses the local sample 40345.pdf) and executes extraction.
        In production (Vercel), the sample PDF is not available; users must upload.
        """
        project_id = str(uuid.uuid4())

        if use_sample_pdf:
            sample_path = BASE_DIR / "40345.pdf"
            if not sample_path.exists():
                raise HTTPException(status_code=400, detail="Sample PDF not available in this deployment.")
            source_bytes = sample_path.read_bytes()
            original_filename = "40345.pdf"
        elif file is not None:
            if not file.filename or not file.filename.lower().endswith(".pdf"):
                raise HTTPException(status_code=400, detail="Only PDF files are allowed.")
            source_bytes = await file.read()
            original_filename = file.filename
        else:
            raise HTTPException(status_code=400, detail="Must provide a PDF file or select use_sample_pdf=true")

        if len(source_bytes) == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        max_upload_mb = int(os.environ.get("MAX_UPLOAD_MB", "50"))
        if len(source_bytes) > max_upload_mb * 1024 * 1024:
            raise HTTPException(
                status_code=413,
                detail=f"File too large. Maximum size is {max_upload_mb} MB."
            )

        rel_key, checksum, _ = filestore.save_source_pdf(
            project_id=project_id,
            source_path_or_bytes=source_bytes,
            original_filename=original_filename,
        )

        name = project_name or f"Inspection - {original_filename}"
        project = repo.create_project(
            project_id=project_id,
            name=name,
            source_filename=original_filename,
            source_storage_key=rel_key,
            source_checksum=checksum,
            language_variant="en-GB",
            status="extracting"
        )

        target_page_numbers: Optional[List[int]] = None
        if pages and pages.strip():
            try:
                cleaned_pages = pages.replace(",", " ")
                target_page_numbers = [int(p) for p in cleaned_pages.split() if p.isdigit()]
            except ValueError:
                target_page_numbers = None

        import tempfile
        tmp_pdf_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(source_bytes)
                tmp_pdf_path = tmp.name

            extraction_res = engine.process_pdf(
                pdf_path=tmp_pdf_path,
                page_numbers=target_page_numbers,
                output_images_dir=None
            )

            for grp in extraction_res.groups:
                repo.create_figure_group(
                    group_id=grp.group_id,
                    project_id=project_id,
                    caption_text=grp.caption_text,
                    caption_page_number=grp.caption_page_number,
                    caption_position=grp.caption_position or "below",
                    figure_label=grp.figure_label,
                    is_cross_page=grp.is_cross_page,
                    review_status=grp.review_status,
                    review_reasons=grp.review_reasons,
                )

            for img in extraction_res.images:
                img_key, img_checksum, _ = filestore.save_image(
                    project_id=project_id,
                    image_id=img.image_id,
                    image_bytes=img.image_bytes,
                    ext=img.image_format
                )

                repo.create_image_asset(
                    image_id=img.image_id,
                    project_id=project_id,
                    page_number=img.page_number,
                    xref=img.xref,
                    placement_index=img.placement_index,
                    bbox=img.bbox.to_dict(),
                    original_caption=img.caption,
                    caption_position=img.caption_position,
                    needs_caption_review=img.needs_caption_review,
                    review_reasons=img.review_reasons,
                    suggested_filename=img.suggested_filename,
                    file_storage_key=img_key,
                    file_checksum=img_checksum,
                    image_format=img.image_format,
                    processing_status="ready_for_review",
                    group_id=img.group_id,
                    association_type=img.association_type,
                    association_confidence=img.association_confidence,
                    panel_label=img.panel_label,
                )

            repo.update_project_status(project_id, status="ready_for_review")

        except Exception as e:
            repo.update_project_status(project_id, status="failed")
            raise HTTPException(status_code=500, detail=f"Extraction failure: {repr(e)}")
        finally:
            if tmp_pdf_path and os.path.exists(tmp_pdf_path):
                try:
                    os.unlink(tmp_pdf_path)
                except OSError:
                    pass

        saved_images = repo.list_project_images(project_id)
        saved_groups = repo.list_project_groups(project_id)

        img_dict_map = {img.image_id: [c.to_dict() for c in img.caption_candidates] for img in extraction_res.images}
        for item in saved_images:
            item["caption_candidates"] = img_dict_map.get(item["id"], [])

        return {
            "project_id": project_id,
            "project_name": name,
            "source_filename": original_filename,
            "total_pages": extraction_res.total_pages,
            "pages_processed": extraction_res.pages_processed,
            "groups": saved_groups,
            "stats": {
                "total_images": len(saved_images),
                "captions_found": sum(1 for img in saved_images if bool(img["original_caption"])),
                "needs_review": sum(1 for img in saved_images if img["needs_caption_review"]),
                "matched": sum(1 for img in saved_images if bool(img["original_caption"]) and not img["needs_caption_review"]),
                "group_matched": sum(1 for img in saved_images if img.get("association_type") == "group_matched"),
                "figure_groups": len(saved_groups),
                "cross_page_groups": sum(1 for g in saved_groups if g["is_cross_page"]),
            },
            "images": saved_images
        }

    # ============================================================
    # AI Generation & Telemetry Endpoints (Phase 4)
    # ============================================================

    @app.get("/api/ai/config")
    async def get_ai_config():
        data = app.state.ai_config.to_safe_dict()
        is_mock = isinstance(app.state.ai_provider, MockAIProvider)
        is_configured = app.state.ai_config.is_configured() and not is_mock
        provider_display = f"{app.state.ai_config.hf_provider} ({app.state.ai_config.hf_model.split('/')[-1]})"
        data["is_configured"] = is_configured
        data["is_mock"] = is_mock
        data["provider_name"] = "Offline Mock" if is_mock else provider_display
        data["default_prompt_template"] = DEFAULT_PROMPT_TEMPLATE
        data["default_prompt_gb"] = resolve_default_prompt("en-GB")
        data["default_prompt_us"] = resolve_default_prompt("en-US")
        data["word_count_min"] = 45
        data["word_count_max"] = 50
        return data

    @app.get("/api/ai/budget")
    async def get_ai_budget():
        return app.state.budget_tracker.get_summary()

    @app.get("/api/ai/usage")
    async def get_ai_usage():
        tracker_summary = app.state.budget_tracker.get_summary()
        ledger_total = app.state.repo.get_total_ai_usage()
        return {
            "tracker": tracker_summary,
            "ledger": ledger_total,
        }

    @app.get("/api/projects/{project_id}/images/{image_id}/versions")
    async def get_image_versions(project_id: str, image_id: str):
        asset = repo.get_image_asset(image_id)
        if not asset or asset["project_id"] != project_id:
            raise HTTPException(status_code=404, detail="Image asset not found")

        versions = repo.list_image_versions(image_id)
        for v in versions:
            v["is_contaminated"] = is_contaminated_alt_text(v.get("text", ""))

        latest = repo.get_latest_version(image_id)
        if latest:
            latest["is_contaminated"] = is_contaminated_alt_text(latest.get("text", ""))
            if latest["is_contaminated"]:
                clean_preview, is_valid, _ = clean_alt_text_response(latest.get("text", ""))
                latest["cleaned_preview"] = clean_preview if is_valid else ""

        approval = repo.get_approval_for_image(image_id)
        is_latest_approved = bool(
            approval and latest and approval.get("alt_text_version_id") == latest.get("id")
            and asset.get("processing_status") == "approved"
        )

        return {
            "image_id": image_id,
            "versions": versions,
            "latest_version": latest,
            "is_approved": is_latest_approved,
            "approval": approval,
            "approved_version_id": approval["alt_text_version_id"] if approval else None,
        }

    @app.post("/api/projects/{project_id}/images/{image_id}/generate")
    async def generate_alt_text(
        project_id: str,
        image_id: str,
        payload: Optional[GenerateRequestPayload] = None,
    ):
        asset = repo.get_image_asset(image_id)
        if not asset or asset["project_id"] != project_id:
            raise HTTPException(status_code=404, detail="Image asset not found")

        user_prompt = payload.user_prompt if payload else None
        language = payload.language if payload else None
        revision_instruction = payload.revision_instruction if payload else None

        try:
            result, version_record = app.state.generator.generate_for_image(
                project_id=project_id,
                image_id=image_id,
                user_prompt=user_prompt,
                language=language,
                revision_instruction=revision_instruction,
            )
            return {
                "status": result.status,
                "result": result.to_dict(),
                "version": version_record,
                "usage_summary": app.state.budget_tracker.get_summary(),
            }
        except BudgetExceededError as e:
            raise HTTPException(
                status_code=429,
                detail={"message": str(e), "is_budget_exceeded": True}
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Generation failed: {str(e)}")

    # ============================================================
    # Figure Group Review & Approval Endpoints (Phase 4.1)
    # ============================================================

    @app.get("/api/projects/{project_id}/groups")
    async def list_project_groups_endpoint(project_id: str):
        project = repo.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        groups = repo.list_project_groups(project_id)
        images = repo.list_project_images(project_id)
        images_by_group = {}
        for img in images:
            gid = img.get("group_id")
            if gid:
                images_by_group.setdefault(gid, []).append(img)
        for grp in groups:
            grp["images"] = images_by_group.get(grp["id"], [])
        return {"groups": groups}

    @app.post("/api/projects/{project_id}/groups/create-from-selection")
    async def create_group_from_selection_endpoint(
        project_id: str,
        payload: CreateGroupFromSelectionPayload,
    ):
        project = repo.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        if not payload.image_ids:
            raise HTTPException(status_code=400, detail="image_ids cannot be empty")

        if len(payload.image_ids) != len(set(payload.image_ids)):
            raise HTTPException(status_code=400, detail="Duplicate image IDs in selection")

        clean_caption = payload.caption.strip() if payload.caption else ""
        if not clean_caption:
            raise HTTPException(status_code=400, detail="Caption cannot be empty or whitespace-only")

        valid_modes = {"merged", "applied_existing", "custom"}
        if payload.decision_mode not in valid_modes:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid decision_mode '{payload.decision_mode}'. Allowed values: {sorted(list(valid_modes))}"
            )

        try:
            result = repo.create_group_from_selection(
                project_id=project_id,
                image_ids=payload.image_ids,
                caption=clean_caption,
                caption_position=payload.caption_position or "below",
                figure_label=payload.figure_label,
                actor_id="editor",
                allow_reassignment=bool(payload.allow_reassignment),
                decision_mode=payload.decision_mode,
            )
        except ValueError as e:
            err_msg = str(e)
            if "not found" in err_msg.lower():
                raise HTTPException(status_code=404, detail=err_msg)
            raise HTTPException(status_code=400, detail=err_msg)
        except Exception as e:
            logger.error(f"Unexpected error in create_group_from_selection: {e}")
            raise HTTPException(status_code=500, detail="Failed to create figure group from selection.")

        return {
            "status": "success",
            "group_id": result["group_id"],
            "caption": clean_caption,
            "member_image_ids": result["member_image_ids"],
            "flagged_for_review": result["flagged_for_review"],
            "group": result["group"],
            "audit_event_id": result.get("audit_event_id"),
        }

    @app.post("/api/projects/{project_id}/groups/{group_id}/confirm")
    async def confirm_group_endpoint(project_id: str, group_id: str):
        grp = repo.get_figure_group(group_id)
        if not grp or grp["project_id"] != project_id:
            raise HTTPException(status_code=404, detail="Figure group not found")
        repo.update_group_review_status(group_id, "confirmed")
        images = repo.list_project_images(project_id)
        for img in images:
            if img.get("group_id") == group_id and img.get("needs_caption_review"):
                repo.update_image_group_association(
                    image_id=img["id"],
                    group_id=group_id,
                    association_type=img.get("association_type") or "group_matched",
                    panel_label=img.get("panel_label"),
                    needs_caption_review=False
                )
        return {"status": "confirmed", "group_id": group_id}

    @app.post("/api/projects/{project_id}/groups/{group_id}/remove-image")
    async def remove_image_from_group_endpoint(project_id: str, group_id: str, payload: GroupImageActionPayload):
        grp = repo.get_figure_group(group_id)
        if not grp or grp["project_id"] != project_id:
            raise HTTPException(status_code=404, detail="Figure group not found")
        asset = repo.get_image_asset(payload.image_id)
        if not asset or asset["project_id"] != project_id:
            raise HTTPException(status_code=404, detail="Image asset not found")
        repo.remove_image_from_group(payload.image_id)
        return {"status": "removed", "image_id": payload.image_id, "group_id": group_id}

    @app.post("/api/projects/{project_id}/groups/{group_id}/add-image")
    async def add_image_to_group_endpoint(project_id: str, group_id: str, payload: GroupImageActionPayload):
        grp = repo.get_figure_group(group_id)
        if not grp or grp["project_id"] != project_id:
            raise HTTPException(status_code=404, detail="Figure group not found")
        asset = repo.get_image_asset(payload.image_id)
        if not asset or asset["project_id"] != project_id:
            raise HTTPException(status_code=404, detail="Image asset not found")
        repo.update_image_group_association(
            image_id=payload.image_id,
            group_id=group_id,
            association_type="group_matched",
            panel_label=payload.panel_label or asset.get("panel_label"),
            needs_caption_review=False,
        )
        caption_val = grp.get("caption_text") or grp.get("caption") or ""
        repo.update_image_caption(
            image_id=payload.image_id,
            caption=caption_val,
            caption_position=grp.get("caption_position") or asset.get("caption_position")
        )
        return {"status": "added", "image_id": payload.image_id, "group_id": group_id}

    @app.post("/api/projects/{project_id}/groups/{group_id}/split")
    async def split_group_endpoint(project_id: str, group_id: str):
        grp = repo.get_figure_group(group_id)
        if not grp or grp["project_id"] != project_id:
            raise HTTPException(status_code=404, detail="Figure group not found")
        images = repo.list_project_images(project_id)
        for img in images:
            if img.get("group_id") == group_id:
                repo.remove_image_from_group(img["id"])
        repo.update_group_review_status(group_id, "split")
        return {"status": "split", "group_id": group_id}

    @app.put("/api/projects/{project_id}/images/{image_id}/association")
    async def update_image_association_endpoint(project_id: str, image_id: str, payload: ImageAssociationUpdatePayload):
        asset = repo.get_image_asset(image_id)
        if not asset or asset["project_id"] != project_id:
            raise HTTPException(status_code=404, detail="Image asset not found")

        if payload.caption is not None:
            repo.update_image_caption(image_id, payload.caption, payload.caption_position)

        repo.update_image_group_association(
            image_id=image_id,
            group_id=payload.group_id,
            association_type=payload.association_type,
            panel_label=payload.panel_label,
            needs_caption_review=payload.needs_review
        )
        updated = repo.get_image_asset(image_id)
        return {"status": "updated", "image": updated}

    @app.put("/api/projects/{project_id}/images/{image_id}/caption")
    async def update_image_caption_endpoint(
        project_id: str,
        image_id: str,
        payload: CaptionUpdatePayload,
    ):
        asset = repo.get_image_asset(image_id)
        if not asset or asset["project_id"] != project_id:
            raise HTTPException(status_code=404, detail="Image asset not found")

        old_caption = (asset.get("original_caption") or "").strip()
        if payload.caption is not None:
            new_caption = payload.caption.strip()
        else:
            new_caption = old_caption
        caption_changed = (new_caption != old_caption)

        approval = repo.get_approval_for_image(image_id)
        raw_reasons = asset.get("review_reasons")
        if isinstance(raw_reasons, list):
            reasons = list(raw_reasons)
        elif isinstance(raw_reasons, str):
            try:
                reasons = json.loads(raw_reasons)
            except Exception:
                reasons = []
        else:
            reasons = []

        def _is_caption_reason(r: str) -> bool:
            r_lower = r.lower()
            return any(kw in r_lower for kw in [
                "caption", "proximity tolerance", "figure prefix",
                "competing figure", "post-approval", "no caption found"
            ])

        if payload.resolve_review or payload.needs_caption_review is False:
            needs_review = False
            reasons = [r for r in reasons if not _is_caption_reason(r)]
        elif payload.needs_caption_review is True:
            needs_review = True
        elif caption_changed and approval:
            needs_review = True
            reason_msg = "Caption modified after alt-text approval; review required"
            if reason_msg not in reasons:
                reasons.append(reason_msg)
            if asset.get("processing_status") == "approved":
                repo.update_image_status(image_id, "needs_review")
        else:
            needs_review = bool(asset.get("needs_caption_review"))

        updated = repo.update_image_caption(
            image_id=image_id,
            caption=new_caption,
            caption_position=asset.get("caption_position"),
            needs_review=needs_review,
            review_reasons=reasons,
        )

        event_type = "caption_review_resolved" if (payload.resolve_review or payload.needs_caption_review is False) else "caption_updated"
        audit_details = {
            "prior_caption": old_caption,
            "new_caption": new_caption,
            "prior_needs_review": bool(asset.get("needs_caption_review")),
            "new_needs_review": needs_review,
            "prior_reasons": asset.get("review_reasons", []),
            "new_reasons": reasons,
            "resolved": bool(payload.resolve_review or payload.needs_caption_review is False),
        }
        repo.record_audit_event(
            event_id=str(uuid.uuid4()),
            project_id=project_id,
            event_type=event_type,
            target_type="image",
            target_id=image_id,
            details=audit_details,
            actor_id="editor",
        )

        return {"status": "updated", "image": updated}

    @app.post("/api/projects/{project_id}/images/{image_id}/draft")
    async def save_manual_draft(
        project_id: str,
        image_id: str,
        payload: DraftSavePayload,
    ):
        asset = repo.get_image_asset(image_id)
        if not asset or asset["project_id"] != project_id:
            raise HTTPException(status_code=404, detail="Image asset not found")

        versions = repo.list_image_versions(image_id)
        next_ver = len(versions) + 1
        v_id = str(uuid.uuid4())
        version_record = repo.create_alt_text_version(
            version_id=v_id,
            image_id=image_id,
            version_number=next_ver,
            text=payload.text.strip(),
            revision_instruction="Manual client edit",
            provider="manual",
            model="human_editor",
            language=payload.language or "en-GB",
            status="success",
        )
        repo.update_image_status(image_id, "drafted")
        return {"status": "saved", "version": version_record}

    @app.post("/api/projects/{project_id}/images/{image_id}/clean-draft")
    async def clean_image_draft_endpoint(
        project_id: str,
        image_id: str,
        payload: Optional[Dict[str, Any]] = None,
    ):
        asset = repo.get_image_asset(image_id)
        if not asset or asset["project_id"] != project_id:
            raise HTTPException(status_code=404, detail="Image asset not found")

        target_text = (payload.get("text") if payload else None)
        if not target_text:
            latest = repo.get_latest_version(image_id)
            target_text = latest["text"] if latest else ""

        if not target_text:
            raise HTTPException(status_code=400, detail="No draft text available to clean.")

        cleaned, is_valid, err_msg = clean_alt_text_response(target_text)
        return {
            "status": "success" if is_valid else "failed",
            "original_text": target_text,
            "cleaned_text": cleaned,
            "is_valid": is_valid,
            "error_message": err_msg,
        }

    @app.post("/api/projects/{project_id}/images/{image_id}/approve")
    async def approve_image_draft(
        project_id: str,
        image_id: str,
        payload: Optional[ApproveRequestPayload] = None,
    ):
        asset = repo.get_image_asset(image_id)
        if not asset or asset["project_id"] != project_id:
            raise HTTPException(status_code=404, detail="Image asset not found")

        version_id = payload.version_id if payload and payload.version_id else None

        if payload and payload.alt_text:
            latest = repo.get_latest_version(image_id)
            if not latest or latest["text"].strip() != payload.alt_text.strip():
                versions = repo.list_image_versions(image_id)
                next_ver = len(versions) + 1
                v_id = str(uuid.uuid4())
                repo.create_alt_text_version(
                    version_id=v_id,
                    image_id=image_id,
                    version_number=next_ver,
                    text=payload.alt_text.strip(),
                    revision_instruction="Approved client edit",
                    provider="manual",
                    model="human_editor",
                    language="en-GB",
                    status="success",
                )
                version_id = v_id

        if not version_id:
            latest = repo.get_latest_version(image_id)
            if not latest:
                raise HTTPException(status_code=400, detail="Cannot approve: No draft version exists for this image.")
            version_id = latest["id"]

        approval_id = str(uuid.uuid4())
        reviewer_id = (payload.reviewer_id if payload and payload.reviewer_id else None) or "editor"
        approval = repo.create_approval(
            approval_id=approval_id,
            image_id=image_id,
            alt_text_version_id=version_id,
            reviewer_id=reviewer_id
        )
        repo.update_image_status(image_id, "approved")

        raw_reasons = asset.get("review_reasons")
        if isinstance(raw_reasons, list):
            reasons = raw_reasons
        elif isinstance(raw_reasons, str):
            try:
                reasons = json.loads(raw_reasons)
            except Exception:
                reasons = []
        else:
            reasons = []

        clean_reasons = [r for r in reasons if "Caption modified after alt-text approval" not in r]
        repo.update_image_caption(
            image_id=image_id,
            caption=asset.get("original_caption") or "",
            caption_position=asset.get("caption_position"),
            needs_review=(len(clean_reasons) > 0),
            review_reasons=clean_reasons,
        )

        return {"status": "approved", "approval": approval}

    # ============================================================
    # DOCX Export Endpoints (Phase C)
    # ============================================================

    @app.get("/api/projects/{project_id}/exports/status")
    async def get_project_export_status(project_id: str):
        project = repo.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        eligibility = check_export_eligibility(repo, project_id)

        def _enrich_export_item(exp: Dict[str, Any]) -> Dict[str, Any]:
            exp["download_url"] = f"/api/projects/{project_id}/exports/{exp['id']}/download"
            if not exp.get("filename"):
                key = exp.get("file_storage_key", "")
                fname = key.split("/")[-1]
                if "_" in fname and len(fname.split("_")[0]) == 36:
                    fname = fname.split("_", 1)[1]
                exp["filename"] = fname

            if not exp.get("size_bytes") and not exp.get("byte_size"):
                try:
                    data = filestore.read_bytes(project_id, exp["file_storage_key"])
                    exp["size_bytes"] = len(data)
                    exp["byte_size"] = len(data)
                except Exception:
                    pass
            elif not exp.get("size_bytes"):
                exp["size_bytes"] = exp.get("byte_size")
            elif not exp.get("byte_size"):
                exp["byte_size"] = exp.get("size_bytes")

            fc = exp.get("figure_count")
            if fc is None or fc == 0:
                try:
                    import zipfile
                    import io as _io
                    data = filestore.read_bytes(project_id, exp["file_storage_key"])
                    with zipfile.ZipFile(_io.BytesIO(data), "r") as z:
                        media_files = [n for n in z.namelist() if n.startswith("word/media/")]
                        if media_files:
                            fc = len(media_files)
                            repo.update_export_figure_count(exp["id"], fc)
                except Exception:
                    pass
                if not fc:
                    fc = exp.get("total_figures_exported") or 0

            exp["figure_count"] = int(fc)
            exp["total_figures_exported"] = int(fc)
            return exp

        latest_export = repo.get_latest_project_export(project_id, "docx")
        if latest_export:
            latest_export = _enrich_export_item(latest_export)
        all_exports = [_enrich_export_item(exp) for exp in repo.list_project_exports(project_id)]

        total_images = eligibility["total_images"]
        approved_count = eligibility["approved_count"]
        unapproved_count = eligibility["unapproved_count"]
        unresolved_reviews = eligibility["unresolved_reviews"]
        can_export = eligibility["can_export"]
        blocking_reasons = eligibility["blocking_reasons"]

        checklist = {
            "has_images": total_images > 0,
            "total_images": total_images,
            "all_approved": (unapproved_count == 0 and total_images > 0),
            "approved_count": approved_count,
            "unapproved_count": unapproved_count,
            "no_unresolved_reviews": (unresolved_reviews == 0),
            "unresolved_reviews_count": unresolved_reviews,
        }

        return {
            "eligibility": eligibility,
            "checklist": checklist,
            "eligible": can_export,
            "can_export": can_export,
            "blocking_reasons": blocking_reasons,
            "latest_export": latest_export,
            "all_exports": all_exports,
            "recent_exports": all_exports,
        }

    @app.post("/api/projects/{project_id}/exports/docx")
    async def export_docx_endpoint(project_id: str):
        project = repo.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        try:
            export_record = export_project_docx(repo, filestore, project_id)
            return {
                "status": "success",
                "export_id": export_record["id"],
                "download_url": export_record.get("download_url", f"/api/projects/{project_id}/exports/{export_record['id']}/download"),
                "figure_count": export_record.get("figure_count", 0),
                "total_figures_exported": export_record.get("total_figures_exported", 0),
                "export": export_record,
            }
        except ValueError as ve:
            raise HTTPException(status_code=400, detail={"message": str(ve)})
        except Exception as e:
            logger.exception("DOCX export failed for project %s", project_id)
            raise HTTPException(status_code=500, detail=f"DOCX export generation failed: {e}")

    @app.get("/api/projects/{project_id}/exports/{export_id}/download")
    async def download_export_endpoint(project_id: str, export_id: str):
        project = repo.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        export_record = repo.get_export(export_id)
        if not export_record or export_record["project_id"] != project_id:
            raise HTTPException(status_code=404, detail="Export not found")

        try:
            data = filestore.read_bytes(project_id, export_record["file_storage_key"])
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Export file missing from storage")
        except Exception as e:
            raise HTTPException(status_code=404, detail=f"File asset missing: {e}")

        repo.update_export_status(export_id, status="delivered", mark_delivered=True)

        filename = Path(export_record["file_storage_key"]).name
        if filename.startswith(f"{export_id}_"):
            filename = filename[len(f"{export_id}_"):]

        return Response(
            content=data,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.web.app:app", host="127.0.0.1", port=8000, reload=True)
