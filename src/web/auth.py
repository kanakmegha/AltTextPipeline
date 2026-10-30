"""
Authentication and Authorization
--------------------------------
Adds session-based authentication to the FastAPI application so that project
records, private documents, uploads, approvals, and downloads are not exposed
to unauthenticated users.

Design:
  - Users table (id, email, password_hash, password_salt, is_active, created_at)
  - Sessions table (id, user_id, token_hash, expires_at, created_at)
  - Login attempts table (id, identifier, ip_address, attempted_at)
  - Login endpoint creates a session and sets a signed HttpOnly cookie
  - Middleware checks the session cookie and CSRF token on protected routes
  - AUTH_ENABLED=false disables auth for local development (default)
  - SESSION_SECRET_KEY (>= 32 bytes) is required when AUTH_ENABLED=true
  - Passwords are hashed with hashlib.pbkdf2_hmac (standard library, no extra dep)

Endpoints:
  POST   /api/auth/login       - authenticate, set session cookie & CSRF cookie
  POST   /api/auth/logout      - invalidate session, clear cookies
  GET    /api/auth/session     - return current session/user info
  GET    /api/auth/csrf-token  - return CSRF token for state-changing requests
"""

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from fastapi import Request, HTTPException, status

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_TRUE_VALUES = {"1", "true", "yes", "on"}
_WEAK_SECRET_SUBSTRINGS = (
    "changeme",
    "placeholder",
    "your-secret",
    "your_secret",
    "replace-me",
    "replace_me",
    "insert-secret",
    "example-secret",
    "default-secret",
)
_WEAK_EXACT_SECRETS = {
    "secret",
    "password",
    "default",
    "development",
    "production",
    "test",
    "testing",
    "admin",
}

MIN_SECRET_KEY_BYTES = 32
MAX_PASSWORD_LENGTH = 256
MIN_PASSWORD_LENGTH = 8

LOGIN_RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("LOGIN_RATE_LIMIT_WINDOW_SECONDS", "900"))
LOGIN_RATE_LIMIT_MAX_ATTEMPTS = int(os.environ.get("LOGIN_RATE_LIMIT_MAX_ATTEMPTS", "5"))

# In-memory fallback rate limiter (used alongside DB persistence)
_MEMORY_LOGIN_ATTEMPTS: Dict[str, List[ float ]] = {}


def _env_auth_enabled() -> bool:
    return os.environ.get("AUTH_ENABLED", "false").strip().lower() in _TRUE_VALUES


_INITIAL_AUTH_ENABLED = _env_auth_enabled()
AUTH_ENABLED: bool = _INITIAL_AUTH_ENABLED
SESSION_SECRET_KEY: str = os.environ.get("SESSION_SECRET_KEY", "")
SESSION_COOKIE_NAME = "alttext_session"
SESSION_COOKIE_MAX_AGE = int(os.environ.get("SESSION_COOKIE_MAX_AGE", "28800"))  # 8 hours
CSRF_TOKEN_NAME = "alttext_csrf"

# Public endpoints that do not require an authenticated session
PUBLIC_PATHS = {
    "/",
    "/api/health",
    "/api/sample-pdf-available",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/session",
    "/api/auth/csrf-token",
}

# Path prefixes that are always public
PUBLIC_PREFIXES = ("/static/", "/docs", "/openapi.json", "/favicon.ico")

SAFE_HTTP_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


def is_auth_enabled() -> bool:
    """Return whether authentication is enabled, respecting runtime env or module overrides."""
    global AUTH_ENABLED, _INITIAL_AUTH_ENABLED
    env_val = _env_auth_enabled()
    if AUTH_ENABLED != _INITIAL_AUTH_ENABLED:
        return bool(AUTH_ENABLED)
    AUTH_ENABLED = env_val
    return env_val


def get_session_secret() -> str:
    """Return the configured SESSION_SECRET_KEY from environment or module attribute."""
    env_secret = os.environ.get("SESSION_SECRET_KEY", "")
    if env_secret:
        return env_secret
    return SESSION_SECRET_KEY or ""


def is_production_env() -> bool:
    """Detect whether the application is running in a production environment."""
    if os.environ.get("VERCEL", "").strip():
        return True
    for key in ("APP_ENV", "ENVIRONMENT", "NODE_ENV", "VERCEL_ENV"):
        if os.environ.get(key, "").strip().lower() == "production":
            return True
    return False


def is_secure_cookie(request: Optional[Request] = None) -> bool:
    """
    Determine whether cookies must be marked Secure.
    Always True in production or over HTTPS; configurable via COOKIE_SECURE.
    """
    explicit = os.environ.get("COOKIE_SECURE", "").strip().lower()
    if explicit in _TRUE_VALUES:
        return True
    if explicit in {"0", "false", "no", "off"} and not is_production_env():
        return False
    if is_production_env():
        return True
    if request is not None:
        if request.url.scheme == "https":
            return True
        forwarded_proto = request.headers.get("x-forwarded-proto", "")
        if forwarded_proto.split(",")[0].strip().lower() == "https":
            return True
    return False


def validate_secret_strength(secret: str) -> None:
    """
    Validate that SESSION_SECRET_KEY is non-empty, not a placeholder, and has
    at least 32 bytes with sufficient character diversity.
    Never includes the secret value in error messages.
    """
    if not secret or not secret.strip():
        raise RuntimeError(
            "Authentication configuration error: SESSION_SECRET_KEY is required when AUTH_ENABLED=true."
        )
    cleaned = secret.strip()
    if cleaned.lower() in _WEAK_EXACT_SECRETS:
        raise RuntimeError(
            "Authentication configuration error: SESSION_SECRET_KEY is a weak or placeholder value."
        )
    for marker in _WEAK_SECRET_SUBSTRINGS:
        if marker in cleaned.lower():
            raise RuntimeError(
                "Authentication configuration error: SESSION_SECRET_KEY contains a placeholder pattern."
            )
    if len(cleaned.encode("utf-8")) < MIN_SECRET_KEY_BYTES:
        raise RuntimeError(
            f"Authentication configuration error: SESSION_SECRET_KEY must be at least {MIN_SECRET_KEY_BYTES} bytes."
        )
    if len(set(cleaned)) < 8:
        raise RuntimeError(
            "Authentication configuration error: SESSION_SECRET_KEY lacks sufficient entropy."
        )


def validate_auth_config(require_auth_enabled: bool = False) -> None:
    """
    Fail-fast startup validation for authentication configuration.
    When require_auth_enabled=True (or in production), AUTH_ENABLED must be true.
    When AUTH_ENABLED=true, SESSION_SECRET_KEY must pass strength checks.
    """
    global AUTH_ENABLED, SESSION_SECRET_KEY, _INITIAL_AUTH_ENABLED
    env_val = _env_auth_enabled()
    if AUTH_ENABLED == _INITIAL_AUTH_ENABLED:
        AUTH_ENABLED = env_val
        _INITIAL_AUTH_ENABLED = env_val
    SESSION_SECRET_KEY = os.environ.get("SESSION_SECRET_KEY", SESSION_SECRET_KEY)

    enabled = is_auth_enabled()
    if (require_auth_enabled or is_production_env()) and not enabled:
        raise RuntimeError(
            "Production security error: AUTH_ENABLED must be set to 'true' in production."
        )

    if enabled:
        validate_secret_strength(get_session_secret())


def _is_public_path(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    for prefix in PUBLIC_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


# ---------------------------------------------------------------------------
# Password hashing & token signing (stdlib pbkdf2_hmac & hmac)
# ---------------------------------------------------------------------------

def _hash_password(password: str, salt: Optional[bytes] = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return dk.hex(), salt.hex()


def _verify_password(password: str, expected_hash_hex: str, salt_hex: str) -> bool:
    if not password or len(password) > MAX_PASSWORD_LENGTH:
        return False
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return secrets.compare_digest(dk.hex(), expected_hash_hex)


def _make_session_token() -> str:
    return secrets.token_urlsafe(32)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _sign(value: str) -> str:
    secret = get_session_secret()
    if not secret:
        if is_auth_enabled():
            raise RuntimeError("SESSION_SECRET_KEY is required to sign tokens when AUTH_ENABLED=true.")
        return value
    sig = hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{value}.{sig}"


def _unsign(signed: str) -> Optional[str]:
    if not signed or not isinstance(signed, str):
        return None
    secret = get_session_secret()
    if not secret:
        if is_auth_enabled():
            return None
        return signed
    if "." not in signed:
        return None
    value, sig = signed.rsplit(".", 1)
    if not value or not sig:
        return None
    expected = hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()
    if secrets.compare_digest(sig, expected):
        return value
    return None


# ---------------------------------------------------------------------------
# CSRF token generation & verification (HMAC session-bound)
# ---------------------------------------------------------------------------

def _csrf_context(session_token: Optional[str]) -> str:
    if session_token:
        return _hash_token(session_token)
    return "preauth"


def _compute_csrf_mac(nonce: str, context: str) -> str:
    secret = get_session_secret() or "local-dev-csrf-fallback-key"
    payload = f"csrf:{context}:{nonce}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def generate_csrf_token(session_token: Optional[str] = None) -> str:
    """
    Generate a cryptographically random CSRF token bound via HMAC-SHA256
    to the active session token (or 'preauth' before login).
    """
    nonce = secrets.token_hex(16)
    context = _csrf_context(session_token)
    mac = _compute_csrf_mac(nonce, context)
    return f"{nonce}.{mac}"


def verify_csrf_token(
    token: str,
    session_token: Optional[str] = None,
    allow_preauth: bool = False,
) -> bool:
    """
    Validate that the CSRF token has a valid HMAC for the current session
    (or pre-auth context when allow_preauth=True) using constant-time comparison.
    """
    if not token or not isinstance(token, str) or "." not in token:
        return False
    nonce, mac = token.rsplit(".", 1)
    if not nonce or not mac:
        return False

    expected_ctx = _csrf_context(session_token)
    expected_mac = _compute_csrf_mac(nonce, expected_ctx)
    if secrets.compare_digest(mac, expected_mac):
        return True

    if allow_preauth and session_token is not None:
        preauth_mac = _compute_csrf_mac(nonce, "preauth")
        if secrets.compare_digest(mac, preauth_mac):
            return True

    return False


# ---------------------------------------------------------------------------
# Auth & CSRF enforcement helpers
# ---------------------------------------------------------------------------

def get_session_cookie(request: Request) -> Optional[str]:
    """Extract and verify the signed session cookie value."""
    cookies = getattr(request, "cookies", None) or {}
    cookie = cookies.get(SESSION_COOKIE_NAME)
    if not cookie:
        return None
    return _unsign(cookie)


def _parse_utc_datetime(val: Any) -> Optional[datetime]:
    if val is None:
        return None
    if isinstance(val, datetime):
        dt = val
    elif isinstance(val, str):
        normalized = val.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        dt = datetime.fromisoformat(normalized)
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def require_auth(request: Request) -> Optional[Dict[str, Any]]:
    """
    Validate session cookie, session expiry, and active user status.
    Returns the current user dict if authenticated, or raises HTTPException(401)
    if auth is enabled and no valid session exists.
    When AUTH_ENABLED is False, returns a default "dev" user.
    """
    if not is_auth_enabled():
        return {"id": "dev", "email": "dev@local", "is_active": True}

    token = get_session_cookie(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    repo = request.state.repo
    session = repo.get_session_by_token(token)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
        )

    now = datetime.now(timezone.utc)
    try:
        expires_at = _parse_utc_datetime(session.get("expires_at"))
    except Exception:
        repo.delete_session(token)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
        )

    if not expires_at or expires_at <= now:
        repo.delete_session(token)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired",
        )

    user = repo.get_user(session["user_id"])
    if not user or not bool(user.get("is_active")):
        repo.delete_session(token)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User inactive or not found",
        )

    return user


def require_csrf(request: Request) -> None:
    """
    Validate CSRF token for state-changing requests when auth is enabled.
    - Reads token exclusively from the X-CSRF-Token request header (never query params).
    - Compares against the CSRF cookie using constant-time comparison.
    - Verifies HMAC binding to the current session token (or pre-auth for login/logout).
    """
    if not is_auth_enabled():
        return

    method = (getattr(request, "method", "GET") or "GET").upper()
    if method in SAFE_HTTP_METHODS:
        return

    headers = getattr(request, "headers", None) or {}
    header_token = headers.get("X-CSRF-Token") or headers.get("x-csrf-token")
    if not header_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token required in X-CSRF-Token header",
        )

    cookies = getattr(request, "cookies", None) or {}
    cookie_token = cookies.get(CSRF_TOKEN_NAME)
    if not cookie_token or not secrets.compare_digest(header_token, cookie_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid CSRF token",
        )

    session_token = get_session_cookie(request)
    path = getattr(getattr(request, "url", None), "path", "") or ""
    allow_preauth = path in {"/api/auth/login", "/api/auth/logout"}

    if not allow_preauth and not session_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid CSRF session context",
        )

    if not verify_csrf_token(
        header_token,
        session_token=session_token,
        allow_preauth=allow_preauth,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token does not match current session",
        )


# ---------------------------------------------------------------------------
# Login rate limiting
# ---------------------------------------------------------------------------

def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def check_login_rate_limit(request: Request, email: str) -> None:
    """Raise HTTP 429 if recent failed login attempts exceed configured threshold."""
    now_dt = datetime.now(timezone.utc)
    window_sec = int(os.environ.get("LOGIN_RATE_LIMIT_WINDOW_SECONDS", str(LOGIN_RATE_LIMIT_WINDOW_SECONDS)))
    max_attempts = int(os.environ.get("LOGIN_RATE_LIMIT_MAX_ATTEMPTS", str(LOGIN_RATE_LIMIT_MAX_ATTEMPTS)))
    since_dt = now_dt - timedelta(seconds=window_sec)
    since_iso = since_dt.isoformat()
    ip_addr = get_client_ip(request)
    ident = (email or "").lower().strip() or "unknown"

    # Check DB-backed attempts first
    repo = getattr(request.state, "repo", None)
    if repo and hasattr(repo, "count_failed_logins"):
        try:
            count = repo.count_failed_logins(ident, ip_addr, since_iso)
            if count >= max_attempts:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many failed login attempts. Please try again later.",
                )
        except HTTPException:
            raise
        except Exception:
            pass

    # Also check in-memory fallback
    now_ts = now_dt.timestamp()
    cutoff = now_ts - window_sec
    for key in (f"email:{ident}", f"ip:{ip_addr}"):
        attempts = [t for t in _MEMORY_LOGIN_ATTEMPTS.get(key, []) if t >= cutoff]
        _MEMORY_LOGIN_ATTEMPTS[key] = attempts
        if len(attempts) >= max_attempts:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed login attempts. Please try again later.",
            )


def record_login_failure(request: Request, email: str) -> None:
    now_ts = datetime.now(timezone.utc).timestamp()
    ip_addr = get_client_ip(request)
    ident = (email or "").lower().strip() or "unknown"

    for key in (f"email:{ident}", f"ip:{ip_addr}"):
        _MEMORY_LOGIN_ATTEMPTS.setdefault(key, []).append(now_ts)

    repo = getattr(request.state, "repo", None)
    if repo and hasattr(repo, "record_failed_login"):
        try:
            repo.record_failed_login(ident, ip_addr)
        except Exception:
            pass


def clear_login_failures(request: Request, email: str) -> None:
    ip_addr = get_client_ip(request)
    ident = (email or "").lower().strip() or "unknown"
    _MEMORY_LOGIN_ATTEMPTS.pop(f"email:{ident}", None)
    _MEMORY_LOGIN_ATTEMPTS.pop(f"ip:{ip_addr}", None)

    repo = getattr(request.state, "repo", None)
    if repo and hasattr(repo, "clear_failed_logins"):
        try:
            repo.clear_failed_logins(ident, ip_addr)
        except Exception:
            pass


def reset_rate_limit_state() -> None:
    """Clear in-memory rate limit state (useful for test isolation)."""
    _MEMORY_LOGIN_ATTEMPTS.clear()


# ---------------------------------------------------------------------------
# Database schema additions (called at startup)
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

CREATE TABLE IF NOT EXISTS login_attempts (
    id TEXT PRIMARY KEY,
    identifier TEXT NOT NULL,
    ip_address TEXT NOT NULL,
    attempted_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sessions_token_hash ON sessions(token_hash);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_login_attempts_ident ON login_attempts(identifier, attempted_at);
CREATE INDEX IF NOT EXISTS idx_login_attempts_ip ON login_attempts(ip_address, attempted_at);
"""


def ensure_auth_schema(db) -> None:
    """Create auth tables if they don't exist (idempotent across SQLite and PostgreSQL)."""
    statements = [s.strip() for s in AUTH_SCHEMA_SQL.split(";") if s.strip()]
    with db.session() as conn:
        if hasattr(conn, "execute"):
            for stmt in statements:
                conn.execute(stmt)
        else:
            with conn.cursor() as cur:
                for stmt in statements:
                    cur.execute(stmt)
