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
import re
import uuid
import secrets
import hashlib
import tempfile
from urllib.parse import urlparse
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
from src.storage.storage_factory import create_storage, resolve_storage_backend
from src.db.migrations import apply_migrations as sqlite_apply_migrations
from src.db.postgres_migrations import apply_migrations as postgres_apply_migrations
from src.db.repository import Repository
from src.db.postgres_repository import PostgresRepository
from src.storage.filestore import (
    FileStore,
    PathTraversalError as FileStorePathTraversalError,
    CleanupBlockedError as FileStoreCleanupBlockedError,
)
from src.storage.base_storage import (
    BaseStorage,
    PathTraversalError as BasePathTraversalError,
    CleanupBlockedError as BaseCleanupBlockedError,
)

PATH_TRAVERSAL_ERRORS = (FileStorePathTraversalError, BasePathTraversalError)
CLEANUP_BLOCKED_ERRORS = (FileStoreCleanupBlockedError, BaseCleanupBlockedError)
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


class UploadAuthorizeRequest(BaseModel):
    filename: str
    size_bytes: int
    content_type: Optional[str] = "application/pdf"
    project_name: Optional[str] = None
    pages: Optional[str] = None


class UploadAuthorizeResponse(BaseModel):
    upload_key: str
    project_id: str
    pathname: str
    handle_upload_url: str
    expires_at: str
    max_size_mb: int


class UploadCompleteRequest(BaseModel):
    project_name: Optional[str] = None
    pages: Optional[str] = None


UPLOAD_CHUNK_SIZE = 4 * 1024 * 1024  # retained for older client compatibility only

DEFAULT_DEV_CORS_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]
ALLOWED_CORS_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
ALLOWED_CORS_HEADERS = ["Accept", "Authorization", "Content-Type", "X-CSRF-Token", "X-Operator-Token"]


def parse_cors_origins(
    raw_origins: Optional[str] = None,
    is_production: Optional[bool] = None,
) -> List[str]:
    """
    Parse and validate comma-separated CORS_ALLOWED_ORIGINS.
    - Rejects wildcard ('*') origins when credentials are enabled.
    - Validates scheme (http/https), netloc, and absence of paths/queries/fragments.
    - Defaults to localhost origins only in non-production environments.
    """
    prod = auth.is_production_env() if is_production is None else bool(is_production)
    effective_raw = os.environ.get("CORS_ALLOWED_ORIGINS") if raw_origins is None else raw_origins

    if effective_raw is not None and effective_raw.strip():
        origins: List[str] = []
        for item in effective_raw.split(","):
            candidate = item.strip()
            if not candidate:
                continue
            if "*" in candidate:
                raise ValueError(
                    "Invalid CORS configuration: wildcard origins ('*') are prohibited when credentials are enabled."
                )
            parsed = urlparse(candidate)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.path not in {"", "/"}
                or parsed.params
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(
                    f"Invalid CORS origin syntax: {candidate!r}. Expected 'http(s)://host[:port]'."
                )
            normalized = f"{parsed.scheme}://{parsed.netloc}"
            if normalized not in origins:
                origins.append(normalized)
        return origins

    if prod:
        vercel_host = (
            os.environ.get("VERCEL_PROJECT_PRODUCTION_URL", "").strip()
            or os.environ.get("VERCEL_URL", "").strip()
        )
        if vercel_host:
            if vercel_host.startswith(("http://", "https://")):
                return parse_cors_origins(vercel_host, is_production=True)
            return [f"https://{vercel_host}"]
        return []

    return list(DEFAULT_DEV_CORS_ORIGINS)


def _sanitize_disposition_filename(filename: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", filename or "download")
    return cleaned.strip("._") or "download"


def create_app(
    db_conn=None,
    storage=None,
    ai_provider: Optional[AIProvider] = None,
    ai_config: Optional[AIConfig] = None,
    # Backward-compatible parameters for existing tests and local dev
    db_path: Optional[str] = None,
    storage_dir: Optional[str] = None,
    cors_allowed_origins: Optional[str] = None,
) -> FastAPI:
    """
    Creates and configures the FastAPI application.
    """
    auth.validate_auth_config()
    allowed_origins = parse_cors_origins(cors_allowed_origins)

    app = FastAPI(
        title="AltTextPipeline - Extraction Inspector",
        description="Local web interface to inspect image extraction, caption matching, and alt-text generation",
        version="0.4.0",
    )

    # ------------------------------------------------------------------
    # Database & storage initialization
    # ------------------------------------------------------------------
    if db_conn is None:
        has_pg_url = bool(
            os.environ.get("DATABASE_URL")
            or os.environ.get("NEON_DB_URL")
            or os.environ.get("POSTGRES_URL")
        )
        backend = "postgres" if has_pg_url else "sqlite"
        if backend == "postgres":
            db_conn = create_connection(backend="postgres")
        else:
            effective_db_path = db_path or os.environ.get("DB_PATH", DEFAULT_DB_PATH)
            db_conn = create_connection(backend="sqlite", db_path=effective_db_path)

    if storage is None:
        effective_storage_dir = storage_dir or os.environ.get("STORAGE_DIR", DEFAULT_STORAGE_DIR)
        storage = create_storage(
            backend=resolve_storage_backend(),
            base_dir=effective_storage_dir,
            blob_token=os.environ.get("BLOB_READ_WRITE_TOKEN"),
            blob_store_id=os.environ.get("BLOB_STORE_ID"),
        )

    # Run migrations appropriate to the backend
    if type(db_conn).__name__ == "PostgresConnection":
        postgres_apply_migrations(db_conn)
        auth.ensure_auth_schema(db_conn)
        repo = PostgresRepository(db_conn)
    else:
        sqlite_apply_migrations(db_conn)
        auth.ensure_auth_schema(db_conn)
        repo = Repository(db_conn)

    filestore = storage

    engine = ExtractionEngine()

    if ai_config is not None:
        cfg = ai_config
    else:
        in_pytest = bool(os.environ.get("PYTEST_CURRENT_TEST"))
        live_tests = os.environ.get("AI_ENABLE_LIVE_TESTS", "").strip().lower() in {"1", "true", "yes"}
        cfg = AIConfig.from_env(read_dotenv=(not in_pytest or live_tests))

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
    # Authentication & CSRF middleware
    # ------------------------------------------------------------------
    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        request.state.repo = repo
        method = (request.method or "GET").upper()

        if method == "OPTIONS":
            return await call_next(request)

        path = request.url.path
        is_public = auth._is_public_path(path)

        if auth.is_auth_enabled():
            if not is_public:
                try:
                    user = await auth.require_auth(request)
                    request.state.user = user
                except HTTPException as exc:
                    return JSONResponse(
                        status_code=exc.status_code,
                        content={"detail": exc.detail},
                        headers=exc.headers or {},
                    )
            else:
                try:
                    request.state.user = await auth.require_auth(request)
                except HTTPException:
                    request.state.user = None

            if method not in auth.SAFE_HTTP_METHODS:
                try:
                    auth.require_csrf(request)
                except HTTPException as exc:
                    return JSONResponse(
                        status_code=exc.status_code,
                        content={"detail": exc.detail},
                        headers=exc.headers or {},
                    )
        else:
            # Explicit Single-User Mode access & CSRF policy
            if not is_public:
                try:
                    user = await auth.require_auth(request)
                    request.state.user = user
                except HTTPException as exc:
                    return JSONResponse(
                        status_code=exc.status_code,
                        content={"detail": exc.detail},
                        headers=exc.headers or {},
                    )
            else:
                request.state.user = auth.get_single_user_operator()

            if method not in auth.SAFE_HTTP_METHODS:
                try:
                    auth.require_csrf(request)
                except HTTPException as exc:
                    return JSONResponse(
                        status_code=exc.status_code,
                        content={"detail": exc.detail},
                        headers=exc.headers or {},
                    )

        response = await call_next(request)
        return response

    # Register CORSMiddleware after auth_middleware so CORSMiddleware wraps outermost
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=ALLOWED_CORS_METHODS,
        allow_headers=ALLOWED_CORS_HEADERS,
    )

    def _authorize_project_access(
        request: Request,
        project_id: str,
        include_id_in_404: bool = False,
    ) -> Dict[str, Any]:
        """
        Verify that the caller is an authorized operator/user, that the project
        exists, and that the project is authorized under the active ownership policy.
        """
        if not auth.is_auth_enabled():
            auth.verify_single_user_access(request)
        project = repo.get_project(project_id)
        if not project:
            msg = f"Project '{project_id}' not found" if include_id_in_404 else "Project not found"
            raise HTTPException(status_code=404, detail=msg)
        user = getattr(request.state, "user", None)
        auth.authorize_project_ownership(project, user)
        return project

    def _resolve_actor_id(request: Request, fallback: str = "editor") -> str:
        if auth.is_auth_enabled():
            user = getattr(request.state, "user", None)
            if user:
                return user.get("email") or user.get("id") or fallback
        return fallback

    # ------------------------------------------------------------------
    # Auth endpoints
    # ------------------------------------------------------------------
    @app.post("/api/auth/login")
    async def login(request: Request, payload: LoginPayload):
        if not auth.is_auth_enabled():
            return JSONResponse({"detail": "Authentication is disabled"}, status_code=400)

        email = (payload.email or "").strip()
        password = payload.password or ""
        if not email or not password:
            raise HTTPException(status_code=400, detail="Email and password are required")
        if len(email) > 254 or len(password) > auth.MAX_PASSWORD_LENGTH:
            raise HTTPException(status_code=400, detail="Invalid credentials format")

        auth.check_login_rate_limit(request, email)

        user = repo.verify_user_password(email, password)
        if not user:
            auth.record_login_failure(request, email)
            raise HTTPException(status_code=401, detail="Invalid email or password")

        auth.clear_login_failures(request, email)

        # Prevent session fixation by invalidating any prior session cookie
        prior_token = auth.get_session_cookie(request)
        if prior_token:
            repo.delete_session(prior_token)

        repo.cleanup_expired_sessions()

        token = auth._make_session_token()
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=auth.SESSION_COOKIE_MAX_AGE)).isoformat()
        session_id = str(uuid.uuid4())
        repo.create_session(session_id, user["id"], token, expires_at)

        signed_token = auth._sign(token)
        csrf_token = auth.generate_csrf_token(session_token=token)
        secure_flag = auth.is_secure_cookie(request)

        response = JSONResponse(
            {
                "status": "logged_in",
                "user": {"id": user["id"], "email": user["email"]},
                "csrf_token": csrf_token,
            }
        )
        response.set_cookie(
            key=auth.SESSION_COOKIE_NAME,
            value=signed_token,
            httponly=True,
            secure=secure_flag,
            samesite="lax",
            max_age=auth.SESSION_COOKIE_MAX_AGE,
            path="/",
        )
        response.set_cookie(
            key=auth.CSRF_TOKEN_NAME,
            value=csrf_token,
            httponly=False,
            secure=secure_flag,
            samesite="lax",
            max_age=auth.SESSION_COOKIE_MAX_AGE,
            path="/",
        )
        return response

    @app.post("/api/auth/logout")
    async def logout(request: Request):
        if not auth.is_auth_enabled():
            return JSONResponse({"detail": "Authentication is disabled"}, status_code=400)
        token = auth.get_session_cookie(request)
        if token:
            repo.delete_session(token)
        secure_flag = auth.is_secure_cookie(request)
        response = JSONResponse({"status": "logged_out"})
        response.delete_cookie(
            key=auth.SESSION_COOKIE_NAME,
            path="/",
            secure=secure_flag,
            httponly=True,
            samesite="lax",
        )
        response.delete_cookie(
            key=auth.CSRF_TOKEN_NAME,
            path="/",
            secure=secure_flag,
            httponly=False,
            samesite="lax",
        )
        return response

    @app.get("/api/auth/session")
    async def get_session(request: Request):
        if not auth.is_auth_enabled():
            try:
                operator = await auth.require_auth(request)
            except HTTPException as exc:
                return JSONResponse({"auth_enabled": False, "single_user_mode": True, "user": None}, status_code=exc.status_code)
            return JSONResponse(
                {
                    "auth_enabled": False,
                    "single_user_mode": True,
                    "user": {"id": operator["id"], "email": operator["email"]},
                }
            )
        try:
            user = await auth.require_auth(request)
            return JSONResponse({"auth_enabled": True, "single_user_mode": False, "user": {"id": user["id"], "email": user["email"]}})
        except HTTPException:
            return JSONResponse({"auth_enabled": True, "single_user_mode": False, "user": None}, status_code=401)

    @app.get("/api/auth/csrf-token")
    async def get_csrf_token(request: Request):
        session_token = auth.get_session_cookie(request) if auth.is_auth_enabled() else None
        existing = request.cookies.get(auth.CSRF_TOKEN_NAME)
        if existing and auth.verify_csrf_token(
            existing,
            session_token=session_token,
            allow_preauth=(session_token is None),
        ):
            csrf_token = existing
        else:
            csrf_token = auth.generate_csrf_token(session_token=session_token)

        secure_flag = auth.is_secure_cookie(request)
        response = JSONResponse(
            {
                "csrf_token": csrf_token,
                "auth_enabled": auth.is_auth_enabled(),
                "single_user_mode": auth.is_single_user_mode(),
            }
        )
        response.set_cookie(
            key=auth.CSRF_TOKEN_NAME,
            value=csrf_token,
            httponly=False,
            secure=secure_flag,
            samesite="lax",
            max_age=auth.SESSION_COOKIE_MAX_AGE,
            path="/",
        )
        return response

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
    async def review_mode_page(request: Request, project_id: str):
        project = _authorize_project_access(request, project_id, include_id_in_404=True)
        review_file = STATIC_DIR / "review.html"
        if not review_file.exists():
            return HTMLResponse("<h2>Review Mode frontend static files missing.</h2>", status_code=404)
        content = review_file.read_text(encoding="utf-8")
        content = content.replace("{{project_id}}", project_id).replace("{{project_name}}", project.get("name") or "Project Review")
        return HTMLResponse(content)

    @app.get("/export/{project_id}", response_class=HTMLResponse)
    async def export_page(request: Request, project_id: str):
        project = _authorize_project_access(request, project_id, include_id_in_404=True)
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
    async def list_projects(request: Request):
        user = getattr(request.state, "user", None)
        if auth.is_auth_enabled():
            owner_id = user["id"] if user else ""
            projects = repo.list_projects(owner_id=owner_id)
        else:
            all_projects = repo.list_projects()
            projects = auth.filter_authorized_projects(all_projects, user)
        return {"projects": projects}

    @app.get("/api/projects/{project_id}")
    async def get_project(request: Request, project_id: str):
        project = _authorize_project_access(request, project_id)
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

    @app.delete("/api/projects/{project_id}")
    async def delete_project_endpoint(
        request: Request,
        project_id: str,
        cancel: bool = True,
    ):
        _authorize_project_access(request, project_id)
        if hasattr(repo, "has_active_jobs"):
            active_jobs = repo.has_active_jobs(project_id)
        else:
            jobs = repo.list_project_jobs(project_id)
            active_jobs = any(j.get("status") in {"queued", "running"} for j in jobs)
        latest_export = repo.get_latest_project_export(project_id, "docx")
        export_delivered = bool(latest_export and latest_export.get("status") == "delivered")

        try:
            filestore.cleanup_project(
                project_id=project_id,
                export_verified_and_delivered=export_delivered,
                project_cancelled=bool(cancel),
                active_jobs_running=active_jobs,
            )
        except CLEANUP_BLOCKED_ERRORS as e:
            raise HTTPException(status_code=409, detail=str(e))
        except Exception:
            logger.error("Storage cleanup failed for project %s", project_id)
            raise HTTPException(status_code=500, detail="Failed to clean up project storage.")

        repo.delete_project(project_id)
        return {"status": "deleted", "project_id": project_id}

    @app.get("/api/projects/{project_id}/images")
    async def list_project_images(request: Request, project_id: str):
        _authorize_project_access(request, project_id)
        images = repo.list_project_images(project_id)
        return {"images": images}
    
    @app.get("/api/debug/blob-check")
    async def debug_blob_check(request: Request):
        project_id = "9bdb8d49-86dc-4b20-9173-886d8e5fd908"
        image_id = "77953ea2-d433-4a6a-b529-682bbe4fd36e"

        _authorize_project_access(request, project_id)

        asset = repo.get_image_asset(image_id)

        if not asset:
            return {"result": "database_record_missing"}

        key = asset["file_storage_key"]

        result = {
            "storage_class": type(filestore).__name__,
            "token_configured": bool(
                os.environ.get("BLOB_READ_WRITE_TOKEN")
            ),
            "store_id": os.environ.get("BLOB_STORE_ID"),
            "database_key": key,
            }

    # --------------------------------------------------
    # 1. Inspect key normalization
    # --------------------------------------------------
        try:
            resolved_key = filestore._safe_key(project_id, key)

            result["raw_key"] = repr(key)
            result["resolved_key"] = repr(resolved_key)
            result["keys_match"] = key == resolved_key

        except Exception as exc:
            result["key_resolution"] = "failed"
            result["key_resolution_error"] = type(exc).__name__
            result["key_resolution_message"] = str(exc)
        return result

    # --------------------------------------------------
    # 2. Test storage abstraction: HEAD
    # --------------------------------------------------
        try:
            metadata = filestore.head(project_id, key)

            result["storage_head"] = "success"
            result["storage_head_pathname"] = getattr(
                metadata, "pathname", None
            )
            result["storage_head_size"] = getattr(
            metadata, "size", None
            )

        except Exception as exc:
            result["storage_head"] = "failed"
            result["storage_head_error"] = type(exc).__name__
            result["storage_head_message"] = str(exc)

    # --------------------------------------------------
    # 3. Test storage abstraction: GET
    # --------------------------------------------------
        try:
            data = filestore.read_bytes(project_id, key)

            result["storage_get"] = "success"
            result["storage_get_bytes"] = len(data)

        except Exception as exc:
            result["storage_get"] = "failed"
            result["storage_get_error"] = type(exc).__name__
            result["storage_get_message"] = str(exc)

    # --------------------------------------------------
    # 4. Test direct SDK using original database key
    # --------------------------------------------------
        try:
            from vercel.blob import sync as blob

            metadata = blob.head_blob(key)

            result["direct_sdk_head"] = "success"
            result["direct_sdk_pathname"] = getattr(
                metadata, "pathname", None
            )
            result["direct_sdk_size"] = getattr(
                metadata, "size", None
            )

        except Exception as exc:
            result["direct_sdk_head"] = "failed"
            result["direct_sdk_error"] = type(exc).__name__
            result["direct_sdk_message"] = str(exc)

    # --------------------------------------------------
    # 5. Test direct SDK using resolved key
    # --------------------------------------------------
        try:
            from vercel.blob import sync as blob

            metadata = blob.head_blob(resolved_key)

            result["resolved_sdk_head"] = "success"
            result["resolved_sdk_pathname"] = getattr(
                metadata, "pathname", None
            )
            result["resolved_sdk_size"] = getattr(
                metadata, "size", None
            )

        except Exception as exc:
            result["resolved_sdk_head"] = "failed"
            result["resolved_sdk_error"] = type(exc).__name__
            result["resolved_sdk_message"] = str(exc)

    # --------------------------------------------------
    # 6. Test direct SDK GET using original database key
    # --------------------------------------------------
        try:
            from vercel.blob import sync as blob

            downloaded = blob.get_blob(
                key,
                access="private",
            )

            result["direct_sdk_get"] = "success"
            result["direct_sdk_bytes"] = len(downloaded.body)

        except Exception as exc:
            result["direct_sdk_get"] = "failed"
            result["direct_sdk_get_error"] = type(exc).__name__
            result["direct_sdk_get_message"] = str(exc)

        return result


    @app.get("/api/projects/{project_id}/images/{image_id}/file")
    @app.head("/api/projects/{project_id}/images/{image_id}/file")
    async def get_image_file(request: Request, project_id: str, image_id: str):
        _authorize_project_access(request, project_id)
        asset = repo.get_image_asset(image_id)
        if not asset or asset["project_id"] != project_id:
            raise HTTPException(status_code=404, detail="Image asset not found")

        try:
            data = filestore.read_bytes(project_id, asset["file_storage_key"])
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Image binary not found in storage")
        except PATH_TRAVERSAL_ERRORS:
            raise HTTPException(status_code=400, detail="Invalid storage key")
        except Exception:
            logger.error("Failed reading image binary %s for project %s", image_id, project_id)
            raise HTTPException(status_code=500, detail="Failed to retrieve image binary")

        media_type = f"image/{asset['image_format']}" if asset["image_format"] != "jpg" else "image/jpeg"
        safe_name = _sanitize_disposition_filename(asset.get("suggested_filename") or "image.png")
        return Response(
            content=data,
            media_type=media_type,
            headers={"Content-Disposition": f'inline; filename="{safe_name}"'}
        )

    @app.post("/api/upload-and-extract")
    async def upload_and_extract(
        request: Request,
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

        if not source_bytes.lstrip().startswith(b"%PDF-"):
            raise HTTPException(status_code=400, detail="Uploaded file is not a valid PDF document.")

        rel_key, checksum, _ = filestore.save_source_pdf(
            project_id=project_id,
            source_path_or_bytes=source_bytes,
            original_filename=original_filename,
        )

        user = getattr(request.state, "user", None) or auth.get_single_user_operator()
        owner_id = user.get("id") if user else auth.get_single_user_id()

        name = project_name or f"Inspection - {original_filename}"
        project = repo.create_project(
            project_id=project_id,
            name=name,
            source_filename=original_filename,
            source_storage_key=rel_key,
            source_checksum=checksum,
            language_variant="en-GB",
            status="extracting",
            owner_id=owner_id,
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

        except Exception:
            logger.error("PDF extraction failed for project %s", project_id)
            repo.update_project_status(project_id, status="failed")
            raise HTTPException(status_code=500, detail="Extraction failure while processing PDF document.")
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
    # Direct browser-to-Blob upload intents
    # ============================================================

    @app.post("/api/uploads/authorize")
    async def upload_authorize(request: Request, payload: UploadAuthorizeRequest):
        user = getattr(request.state, "user", None) or auth.get_single_user_operator()
        owner_id = user.get("id") if user else auth.get_single_user_id()
        filename = (payload.filename or "").strip()
        if (not filename or len(filename) > 255 or any(ord(ch) < 32 for ch in filename)
                or filename != Path(filename).name or "/" in filename or "\\" in filename
                or not filename.lower().endswith(".pdf")):
            raise HTTPException(status_code=400, detail="Only a plain PDF filename is allowed.")
        if payload.content_type != "application/pdf":
            raise HTTPException(status_code=400, detail="Invalid content type. Expected application/pdf.")
        max_bytes = int(os.environ.get("MAX_UPLOAD_MB", "50")) * 1024 * 1024
        if payload.size_bytes <= 0 or payload.size_bytes > max_bytes:
            raise HTTPException(status_code=413, detail="Invalid PDF size or file exceeds the upload limit.")
        upload_key = str(uuid.uuid4())
        project_id = str(uuid.uuid4())
        pathname = f"{project_id}/source/{upload_key}.pdf"
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
        record = repo.create_upload_key(
            upload_key=upload_key, content_type="application/pdf", expires_at=expires_at,
            owner_id=owner_id, filename=filename, expected_size=payload.size_bytes,
            blob_key=pathname,
        )
        if not record:
            raise HTTPException(status_code=500, detail="Could not create upload intent.")
        return {
            "upload_key": upload_key, "project_id": project_id, "pathname": pathname,
            "handle_upload_url": "/api/blob-upload", "expires_at": expires_at,
            "max_size_bytes": max_bytes,
        }

    @app.post("/api/uploads/{upload_key}/claim-token")
    async def claim_upload_token(request: Request, upload_key: str):
        """One-use token gate called by the Node handleUpload route."""
        user = getattr(request.state, "user", None) or auth.get_single_user_operator()
        owner_id = user.get("id") if user else auth.get_single_user_id()
        record = repo.get_upload_key(upload_key)
        if not record or record.get("owner_id") != owner_id:
            raise HTTPException(status_code=404, detail="Upload intent not found.")
        if record.get("status") != "uploading":
            raise HTTPException(status_code=409, detail="Upload intent is no longer available.")
        try:
            expiry = datetime.fromisoformat(record["expires_at"].replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
        except Exception:
            raise HTTPException(status_code=410, detail="Upload intent expired.")
        if expiry <= datetime.now(timezone.utc):
            repo.update_upload_key(upload_key, status="expired")
            raise HTTPException(status_code=410, detail="Upload intent expired.")
        now = datetime.now(timezone.utc).isoformat()
        if not repo.claim_upload_token(upload_key, owner_id, now):
            raise HTTPException(status_code=409, detail="Upload token has already been issued.")
        return {"pathname": record["blob_key"], "max_size_bytes": int(record["expected_size"]),
                "valid_until": int(expiry.timestamp() * 1000)}

    @app.post("/api/uploads/{upload_key}/complete")
    async def upload_complete(request: Request, upload_key: str, payload: Optional[UploadCompleteRequest] = None):
        user = getattr(request.state, "user", None) or auth.get_single_user_operator()
        owner_id = user.get("id") if user else auth.get_single_user_id()
        record = repo.get_upload_key(upload_key)
        if not record or record.get("owner_id") != owner_id:
            raise HTTPException(status_code=404, detail="Upload intent not found.")
        if record.get("status") == "completed" and record.get("project_id"):
            project = repo.get_project(record["project_id"])
            return {"project_id": record["project_id"], "status": project.get("status", "queued"), "already_completed": True}
        if record.get("status") not in {"uploading", "finalizing"} or not record.get("token_issued"):
            raise HTTPException(status_code=409, detail="Upload token was not issued or intent is no longer active.")
        project_name = ((payload.project_name if payload else None) or f"Inspection - {record['filename']}").strip()
        raw_pages = (payload.pages if payload else None) or ""
        page_tokens = raw_pages.replace(",", " ").split()
        if any(not token.isdecimal() or int(token) < 1 for token in page_tokens):
            raise HTTPException(status_code=400, detail="Invalid page selection.")
        target_pages = sorted({int(token) for token in page_tokens})
        expiry = datetime.fromisoformat(record["expires_at"].replace("Z", "+00:00"))
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if record.get("status") == "uploading" and expiry <= datetime.now(timezone.utc):
            repo.update_upload_key(upload_key, status="expired")
            raise HTTPException(status_code=410, detail="Upload intent expired.")
        project_id = record["blob_key"].split("/", 1)[0]
        relative_key = "/".join(record["blob_key"].split("/")[1:])
        try:
            metadata = filestore.head(project_id, relative_key)
            meta_size = metadata.get("size") if isinstance(metadata, dict) else metadata.size
            meta_type = metadata.get("content_type") if isinstance(metadata, dict) else metadata.content_type
            meta_path = metadata.get("pathname") if isinstance(metadata, dict) else metadata.pathname
            meta_etag = metadata.get("etag") if isinstance(metadata, dict) else metadata.etag
            if meta_path and meta_path != record["blob_key"]:
                raise HTTPException(status_code=400, detail="Uploaded object path does not match the upload intent.")
            if int(meta_size) != int(record["expected_size"]):
                raise HTTPException(status_code=400, detail="Uploaded object size does not match the upload intent.")
            if meta_type != "application/pdf":
                raise HTTPException(status_code=400, detail="Uploaded object content type does not match the upload intent.")
            source_size = int(meta_size)
            # Vercel Blob's ETag is metadata, not necessarily a SHA-256 digest.
            # The Render worker computes and stores SHA-256 after download.
            checksum = meta_etag or hashlib.sha256(f"{record['blob_key']}:{source_size}".encode()).hexdigest()
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Blob verification failed for upload %s", upload_key)
            raise HTTPException(status_code=502, detail="Could not verify uploaded Blob object.") from exc

        if record.get("status") == "uploading":
            now = datetime.now(timezone.utc).isoformat()
            if not repo.claim_upload_finalization(upload_key, owner_id, now):
                refreshed = repo.get_upload_key(upload_key)
                if refreshed and refreshed.get("status") == "completed" and refreshed.get("project_id"):
                    project = repo.get_project(refreshed["project_id"])
                    jobs = repo.list_project_jobs(refreshed["project_id"])
                    return {
                        "project_id": refreshed["project_id"],
                        "job_id": jobs[-1]["id"] if jobs else None,
                        "status": project.get("status", "queued"),
                        "already_completed": True,
                    }
                if not refreshed or refreshed.get("status") != "finalizing":
                    raise HTTPException(status_code=409, detail="Upload is already being finalized.")

        job_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"alttext-extraction:{upload_key}"))
        project_preexisting = repo.get_project(project_id) is not None
        finalized = repo.finalize_upload_project(
            upload_key=upload_key, project_id=project_id, job_id=job_id, owner_id=owner_id,
            name=project_name, source_filename=record["filename"], source_storage_key=relative_key,
            source_checksum=checksum, source_size=source_size,
            page_start=min(target_pages) if target_pages else None,
            page_end=max(target_pages) if target_pages else None,
            page_numbers=target_pages or None,
        )
        project = finalized["project"]
        if not project_preexisting:
            repo.record_audit_event(event_id=str(uuid.uuid4()), project_id=project_id,
                event_type="project_created", target_type="project", target_id=project_id,
                details={"source_filename": record["filename"], "source_size": source_size,
                         "upload_key": upload_key, "target_pages": target_pages or None}, actor_id=owner_id)
        try:
            if isinstance(repo, PostgresRepository):
                from src.worker.queueing import publish_extraction_job
                await publish_extraction_job(job_id)
        except Exception:
            # The durable queued row is the outbox; worker reconciliation will publish/claim it.
            logger.exception("Queue publish failed for job %s; database recovery will retry", job_id)
        return {"project_id": project["id"], "job_id": job_id, "status": "queued", "already_completed": False}

    @app.get("/api/jobs/{job_id}/progress")
    async def get_job_progress(request: Request, job_id: str):
        job = repo.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        project = _authorize_project_access(request, job["project_id"])
        batches = repo.list_job_batches(job_id)
        images = repo.list_project_images(job["project_id"])
        return {
            "job_id": job_id,
            "project_id": job["project_id"],
            "job_status": job["status"],
            "project_status": project.get("status"),
            "total_pages": job.get("total_pages", 0),
            "pages_to_process": job.get("pages_to_process", 0),
            "completed_pages": job.get("completed_pages", 0),
            "total_batches": job.get("total_batches", 0),
            "completed_batches": job.get("completed_batches", 0),
            "total_images": len(images),
            "failed_batches": sum(1 for batch in batches if batch["status"] == "failed"),
            "batches": batches,
            "error": job.get("error_message"),
        }

    @app.get("/api/jobs/{job_id}/images")
    async def get_job_images(request: Request, job_id: str):
        job = repo.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        _authorize_project_access(request, job["project_id"])
        return {"images": repo.list_project_images(job["project_id"])}

    @app.get("/api/projects/{project_id}/status")
    async def get_project_status(request: Request, project_id: str):
        """
        Get project processing status. Used by the frontend to poll
        for extraction completion.
        """
        project = repo.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found.")

        # Authorize access
        user = getattr(request.state, "user", None)
        if auth.is_auth_enabled():
            if not user:
                raise HTTPException(status_code=401, detail="Authentication required")
            if project.get("owner_id") != user.get("id"):
                raise HTTPException(status_code=403, detail="Not authorized to access this project")
        else:
            auth.verify_single_user_access(request)
            if project.get("owner_id") not in auth.get_allowed_single_user_owners():
                raise HTTPException(status_code=403, detail="Not authorized to access this project")

        # Get latest job for this project
        jobs = repo.list_project_jobs(project_id)
        latest_job = jobs[0] if jobs else None

        images = repo.list_project_images(project_id)
        groups = repo.list_project_groups(project_id)

        return {
            "project_id": project_id,
            "status": project.get("status"),
            "cleanup_status": project.get("cleanup_status"),
            "total_images": len(images),
            "groups": len(groups),
            "job": latest_job,
            "images": images,
            "groups": groups,
            "error": latest_job.get("error_message") if latest_job and latest_job.get("status") == "failed" else None,
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
    async def get_image_versions(request: Request, project_id: str, image_id: str):
        _authorize_project_access(request, project_id)
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
        request: Request,
        project_id: str,
        image_id: str,
        payload: Optional[GenerateRequestPayload] = None,
    ):
        _authorize_project_access(request, project_id)
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
        except Exception:
            logger.error("Alt-text generation failed for image %s", image_id)
            raise HTTPException(status_code=500, detail="Generation failed due to an internal error.")

    # ============================================================
    # Figure Group Review & Approval Endpoints (Phase 4.1)
    # ============================================================

    @app.get("/api/projects/{project_id}/groups")
    async def list_project_groups_endpoint(request: Request, project_id: str):
        _authorize_project_access(request, project_id)
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
        request: Request,
        project_id: str,
        payload: CreateGroupFromSelectionPayload,
    ):
        _authorize_project_access(request, project_id)

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

        actor_id = _resolve_actor_id(request, "editor")
        try:
            result = repo.create_group_from_selection(
                project_id=project_id,
                image_ids=payload.image_ids,
                caption=clean_caption,
                caption_position=payload.caption_position or "below",
                figure_label=payload.figure_label,
                actor_id=actor_id,
                allow_reassignment=bool(payload.allow_reassignment),
                decision_mode=payload.decision_mode,
            )
        except ValueError as e:
            err_msg = str(e)
            if "not found" in err_msg.lower():
                raise HTTPException(status_code=404, detail=err_msg)
            raise HTTPException(status_code=400, detail=err_msg)
        except Exception:
            logger.error("Unexpected error in create_group_from_selection for project %s", project_id)
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
    async def confirm_group_endpoint(request: Request, project_id: str, group_id: str):
        _authorize_project_access(request, project_id)
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
    async def remove_image_from_group_endpoint(request: Request, project_id: str, group_id: str, payload: GroupImageActionPayload):
        _authorize_project_access(request, project_id)
        grp = repo.get_figure_group(group_id)
        if not grp or grp["project_id"] != project_id:
            raise HTTPException(status_code=404, detail="Figure group not found")
        asset = repo.get_image_asset(payload.image_id)
        if not asset or asset["project_id"] != project_id:
            raise HTTPException(status_code=404, detail="Image asset not found")
        repo.remove_image_from_group(payload.image_id)
        return {"status": "removed", "image_id": payload.image_id, "group_id": group_id}

    @app.post("/api/projects/{project_id}/groups/{group_id}/add-image")
    async def add_image_to_group_endpoint(request: Request, project_id: str, group_id: str, payload: GroupImageActionPayload):
        _authorize_project_access(request, project_id)
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
    async def split_group_endpoint(request: Request, project_id: str, group_id: str):
        _authorize_project_access(request, project_id)
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
    async def update_image_association_endpoint(request: Request, project_id: str, image_id: str, payload: ImageAssociationUpdatePayload):
        _authorize_project_access(request, project_id)
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
        request: Request,
        project_id: str,
        image_id: str,
        payload: CaptionUpdatePayload,
    ):
        _authorize_project_access(request, project_id)
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
        actor_id = _resolve_actor_id(request, "editor")
        repo.record_audit_event(
            event_id=str(uuid.uuid4()),
            project_id=project_id,
            event_type=event_type,
            target_type="image",
            target_id=image_id,
            details=audit_details,
            actor_id=actor_id,
        )

        return {"status": "updated", "image": updated}

    @app.post("/api/projects/{project_id}/images/{image_id}/draft")
    async def save_manual_draft(
        request: Request,
        project_id: str,
        image_id: str,
        payload: DraftSavePayload,
    ):
        _authorize_project_access(request, project_id)
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
        request: Request,
        project_id: str,
        image_id: str,
        payload: Optional[Dict[str, Any]] = None,
    ):
        _authorize_project_access(request, project_id)
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
        request: Request,
        project_id: str,
        image_id: str,
        payload: Optional[ApproveRequestPayload] = None,
    ):
        _authorize_project_access(request, project_id)
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
        if auth.is_auth_enabled():
            reviewer_id = _resolve_actor_id(request, "editor")
        else:
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
    async def get_project_export_status(request: Request, project_id: str):
        _authorize_project_access(request, project_id)
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
    async def export_docx_endpoint(request: Request, project_id: str):
        _authorize_project_access(request, project_id)
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
        except Exception:
            logger.exception("DOCX export failed for project %s", project_id)
            raise HTTPException(status_code=500, detail="DOCX export generation failed.")

    @app.get("/api/projects/{project_id}/exports/{export_id}/download")
    async def download_export_endpoint(request: Request, project_id: str, export_id: str):
        _authorize_project_access(request, project_id)
        export_record = repo.get_export(export_id)
        if not export_record or export_record["project_id"] != project_id:
            raise HTTPException(status_code=404, detail="Export not found")

        try:
            data = filestore.read_bytes(project_id, export_record["file_storage_key"])
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Export file missing from storage")
        except PATH_TRAVERSAL_ERRORS:
            raise HTTPException(status_code=400, detail="Invalid storage key")
        except Exception:
            logger.error("Failed reading export binary %s for project %s", export_id, project_id)
            raise HTTPException(status_code=404, detail="Export file asset missing from storage")

        repo.update_export_status(export_id, status="delivered", mark_delivered=True)

        filename = Path(export_record["file_storage_key"]).name
        if filename.startswith(f"{export_id}_"):
            filename = filename[len(f"{export_id}_"):]
        safe_filename = _sanitize_disposition_filename(filename)

        return Response(
            content=data,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'},
        )

    return app





if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.web.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=8000,
        reload=True,
    )
