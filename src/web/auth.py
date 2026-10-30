"""
Authentication and Authorization
--------------------------------
Adds session-based authentication to the FastAPI application so that project
records, private documents, uploads, approvals, and downloads are not exposed
to unauthenticated users.

Design:
  - Users table (id, email, password_hash, is_active, created_at)
  - Sessions table (id, user_id, token_hash, expires_at, created_at)
  - Login endpoint creates a session and sets a secure HttpOnly cookie
  - Middleware checks the session cookie on all protected /api/ routes
  - AUTH_ENABLED=false disables auth for local development (default)
  - SESSION_SECRET_KEY is used to derive per-request signing (set in production)
  - Passwords are hashed with hashlib.pbkdf2_hmac (standard library, no extra dep)

Endpoints:
  POST   /api/auth/login       - authenticate, set session cookie
  POST   /api/auth/logout      - invalidate session, clear cookie
  GET    /api/auth/session     - return current session/user info
  GET    /api/auth/csrf-token  - return CSRF token for state-changing requests
"""

import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AUTH_ENABLED = os.environ.get("AUTH_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
SESSION_SECRET_KEY = os.environ.get("SESSION_SECRET_KEY", "")
SESSION_COOKIE_NAME = "alttext_session"
SESSION_COOKIE_MAX_AGE = int(os.environ.get("SESSION_COOKIE_MAX_AGE", "28800"))  # 8 hours
CSRF_TOKEN_NAME = "alttext_csrf"

# Public endpoints that do not require authentication
PUBLIC_PATHS = {
    "/api/health",
    "/api/sample-pdf-available",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/session",
    "/api/auth/csrf-token",
}

# Path prefixes that are always public
PUBLIC_PREFIXES = ("/static/", "/docs", "/openapi.json", "/favicon.ico")


def _is_public_path(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    for prefix in PUBLIC_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


# ---------------------------------------------------------------------------
# Password hashing (stdlib pbkdf2_hmac, no extra dependency)
# ---------------------------------------------------------------------------

def _hash_password(password: str, salt: Optional[bytes] = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return dk.hex(), salt.hex()


def _verify_password(password: str, expected_hash_hex: str, salt_hex: str) -> bool:
    salt = bytes.fromhex(salt_hex)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return secrets.compare_digest(dk.hex(), expected_hash_hex)


def _make_session_token() -> str:
    return secrets.token_urlsafe(32)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _sign(value: str) -> str:
    secret = os.environ.get("SESSION_SECRET_KEY", "")
    if not secret:
        return value
    sig = hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{value}.{sig}"


def _unsign(signed: str) -> Optional[str]:
    secret = os.environ.get("SESSION_SECRET_KEY", "")
    if not secret:
        return signed
    if "." not in signed:
        return None
    value, sig = signed.rsplit(".", 1)
    expected = hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()
    if secrets.compare_digest(sig, expected):
        return value
    return None


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def get_session_cookie(request: Request) -> Optional[str]:
    """Extract and verify the session cookie value."""
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if not cookie:
        return None
    return _unsign(cookie)


async def require_auth(request: Request) -> Optional[Dict[str, Any]]:
    """
    Dependency-style auth check. Returns the current user dict if authenticated,
    or raises HTTPException(401) if auth is enabled and no valid session exists.
    When AUTH_ENABLED is False, returns a default "dev" user.
    """
    if not AUTH_ENABLED:
        return {"id": "dev", "email": "dev@local", "is_active": True}

    token = get_session_cookie(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Look up session in database
    repo = request.state.repo
    session = repo.get_session_by_token(token)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
        )

    now = datetime.now(timezone.utc)
    expires_at = session.get("expires_at")
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at)
    if expires_at and expires_at <= now:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired",
        )

    user = repo.get_user(session["user_id"])
    if not user or not user.get("is_active"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User inactive or not found",
        )

    return user


def require_csrf(request: Request) -> None:
    """Validate CSRF token for state-changing requests when auth is enabled."""
    if not AUTH_ENABLED:
        return
    token = request.headers.get("X-CSRF-Token") or request.query_params.get("csrf_token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token required",
        )
    cookie_token = request.cookies.get(CSRF_TOKEN_NAME)
    if not cookie_token or not secrets.compare_digest(token, cookie_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid CSRF token",
        )


# ---------------------------------------------------------------------------
# Database schema additions (called at startup when AUTH_ENABLED)
# ---------------------------------------------------------------------------

AUTH_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sessions_token_hash ON sessions(token_hash);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);
"""


def ensure_auth_schema(db) -> None:
    """Create auth tables if they don't exist (idempotent)."""
    with db.session() as conn:
        for statement in AUTH_SCHEMA_SQL.split(";"):
            stmt = statement.strip()
            if stmt:
                conn.execute(stmt)
