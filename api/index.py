import logging
import os
import sys

REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)

if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger("alttext.vercel")


def validate_environment():
    database_url = (
        os.environ.get("DATABASE_URL")
        or os.environ.get("NEON_DB_URL")
        or os.environ.get("POSTGRES_URL")
    )

    if not database_url:
        raise RuntimeError(
            "Missing database configuration. "
            "Set DATABASE_URL, NEON_DB_URL, or POSTGRES_URL."
        )

    if not os.environ.get("BLOB_READ_WRITE_TOKEN"):
        raise RuntimeError(
            "Missing required environment variable: "
            "BLOB_READ_WRITE_TOKEN"
        )

    from src.web import auth
    from src.web.app import parse_cors_origins

    # Validate single-user or multi-user auth configuration and require SESSION_SECRET_KEY in production
    auth.validate_auth_config(require_auth_enabled=False, require_secret_key=True)

    # Validate CORS allowed origins syntax & reject wildcards
    parse_cors_origins(os.environ.get("CORS_ALLOWED_ORIGINS"), is_production=True)

    return database_url


def build_app():
    database_url = validate_environment()

    from src.db.connection_factory import create_connection
    from src.storage.storage_factory import create_storage
    from src.web.app import create_app

    logger.info("Connecting to PostgreSQL.")

    db_conn = create_connection(
        "postgres",
        database_url=database_url,
    )

    logger.info("Initializing Vercel Blob storage.")

    storage = create_storage(
        "blob",
        blob_token=os.environ["BLOB_READ_WRITE_TOKEN"],
        blob_store_id=os.environ.get("BLOB_STORE_ID"),
    )

    logger.info("Creating AltTextPipeline application.")

    return create_app(
        db_conn=db_conn,
        storage=storage,
    )


app = build_app()
handler = app