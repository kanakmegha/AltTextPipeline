"""
Vercel Python Function Entrypoint for AltTextPipeline
-----------------------------------------------------
Deploys the existing FastAPI application (src.web.app.create_app) on Vercel
Python Functions, backed by managed PostgreSQL (Vercel Marketplace) and
Vercel Blob for private object storage.

This entrypoint does NOT duplicate application initialization. It imports and
configures the existing application factory with production-ready database
and storage adapters selected via environment variables.

Environment variables required:
  DATABASE_URL        - PostgreSQL connection string (Vercel Postgres)
  BLOB_READ_WRITE_TOKEN - Vercel Blob read/write token
  BLOB_STORE_ID       - Vercel Blob store ID (optional; derived from token if omitted)
  HF_TOKEN            - Hugging Face Inference API token (server-side only)
  HF_MODEL            - Hugging Face model ID (default: zai-org/GLM-5.3-Flash)
  HF_PROVIDER         - HF provider routing (default: novita)
  ALT_TEXT_LANGUAGE   - en-GB or en-US (default: en-GB)
  AI_MAX_MONTHLY_SPEND_USD - Monthly AI spend ceiling (default: 2.00)
  SECRET_KEY          - Session signing key for authentication (REQUIRED in production)
  ADMIN_API_KEY       - Optional API key for service-to-service calls

Local development:
  Use run_inspector.py (uvicorn + SQLite + local file storage).
  This entrypoint is only used on Vercel.
"""

import os
import sys
import logging

# Ensure the repository root is on the Python path so `src` and `api` are importable
# regardless of how Vercel sets the working directory.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("alttext.vercel")

from src.db.connection_factory import create_connection  # noqa: E402
from src.storage.storage_factory import create_storage  # noqa: E402
from src.web.app import create_app  # noqa: E402


def _get_bool(env_key: str, default: str = "false") -> bool:
    return os.environ.get(env_key, default).strip().lower() in {"1", "true", "yes", "on"}


def build_app():
    """
    Build the FastAPI application for production deployment.

    Reads DATABASE_URL and BLOB_READ_WRITE_TOKEN from the environment to select
    PostgreSQL and Vercel Blob adapters. Falls back to SQLite + local storage
    only when those are not configured (useful for review/preview deployments).
    """
    database_url = os.environ.get("DATABASE_URL")
    blob_token = os.environ.get("BLOB_READ_WRITE_TOKEN")
    blob_store_id = os.environ.get("BLOB_STORE_ID")

    if database_url:
        logger.info("Production mode: PostgreSQL + Vercel Blob storage")
        db_conn = create_connection("postgres", database_url=database_url)
        storage = create_storage(
            "blob",
            blob_token=blob_token,
            blob_store_id=blob_store_id,
        )
    else:
        logger.warning(
            "DATABASE_URL not set — falling back to SQLite + local storage "
            "(development/preview only)"
        )
        db_path = os.environ.get("DB_PATH", "/tmp/alttext_pipeline.db")
        storage_dir = os.environ.get("STORAGE_DIR", "/tmp/alttext_storage")
        db_conn = create_connection("sqlite", db_path=db_path)
        storage = create_storage("local", base_dir=storage_dir)

    cfg_kwargs = {}
    # AI configuration is loaded from environment by AIConfig.from_env()
    app = create_app(
        db_conn=db_conn,
        storage=storage,
    )
    return app


app = build_app()

# Vercel Python Functions expects a top-level `app` or `handler` variable.
handler = app

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api.index:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
