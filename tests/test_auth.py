"""
Authentication Tests
--------------------
Tests the session-based authentication system:
- Password hashing and verification
- User creation and lookup
- Session creation, validation, and expiry
- CSRF token handling
- Auth middleware behavior (enabled/disabled)

Tests use the SQLite repository directly (no HTTP server needed).
"""

import os
import uuid
import pytest
from datetime import datetime, timezone, timedelta

# Force auth disabled for most tests unless explicitly enabled
os.environ["AUTH_ENABLED"] = "false"

from src.db.connection import DatabaseConnection
from src.db.migrations import apply_migrations
from src.db.repository import Repository
from src.web import auth


@pytest.fixture
def env(tmp_path):
    db_file = str(tmp_path / "auth_test.db")
    storage_dir = str(tmp_path / "auth_storage")
    db_conn = DatabaseConnection(db_file)
    apply_migrations(db_conn)
    # Also create auth tables
    auth.ensure_auth_schema(db_conn)
    repo = Repository(db_conn)
    return {"db_conn": db_conn, "repo": repo, "tmp_path": tmp_path}


# ------------------------------------------------------------------
# Password hashing
# ------------------------------------------------------------------

def test_password_hash_and_verify():
    password = "secure_password_123"
    h, s = auth._hash_password(password)
    assert h != password
    assert s  # salt is non-empty
    assert auth._verify_password(password, h, s) is True
    assert auth._verify_password("wrong_password", h, s) is False


def test_password_hash_is_deterministic_with_same_salt():
    password = "same_password"
    salt = b"\x01" * 16
    h1, _ = auth._hash_password(password, salt)
    h2, _ = auth._hash_password(password, salt)
    assert h1 == h2


def test_hash_token_is_deterministic():
    token = "abc123"
    assert auth._hash_token(token) == auth._hash_token(token)
    assert auth._hash_token(token) != auth._hash_token("different")


# ------------------------------------------------------------------
# Sign/unsign
# ------------------------------------------------------------------

def test_sign_unsign_with_secret():
    os.environ["SESSION_SECRET_KEY"] = "test_secret_key_for_signing"
    try:
        signed = auth._sign("my_token")
        assert "." in signed
        assert auth._unsign(signed) == "my_token"
        assert auth._unsign("tampered.signature") is None
    finally:
        del os.environ["SESSION_SECRET_KEY"]


def test_sign_unsign_without_secret():
    os.environ.pop("SESSION_SECRET_KEY", None)
    assert auth._sign("my_token") == "my_token"
    assert auth._unsign("my_token") == "my_token"


# ------------------------------------------------------------------
# User CRUD
# ------------------------------------------------------------------

def test_create_and_get_user(env):
    repo = env["repo"]
    user_id = str(uuid.uuid4())
    user = repo.create_user(user_id, "alice@example.com", "password123")
    assert user is not None
    assert user["id"] == user_id
    assert user["email"] == "alice@example.com"
    assert bool(user["is_active"]) is True

    fetched = repo.get_user(user_id)
    assert fetched is not None
    assert fetched["email"] == "alice@example.com"

    assert repo.get_user(str(uuid.uuid4())) is None


def test_get_user_by_email(env):
    repo = env["repo"]
    user_id = str(uuid.uuid4())
    repo.create_user(user_id, "bob@example.com", "pass")

    user = repo.get_user_by_email("bob@example.com")
    assert user is not None
    assert user["id"] == user_id

    assert repo.get_user_by_email("nonexistent@example.com") is None


def test_create_duplicate_email_fails(env):
    repo = env["repo"]
    repo.create_user(str(uuid.uuid4()), "dup@example.com", "pass")
    with pytest.raises(Exception):
        repo.create_user(str(uuid.uuid4()), "dup@example.com", "pass2")


# ------------------------------------------------------------------
# Password verification
# ------------------------------------------------------------------

def test_verify_user_password(env):
    repo = env["repo"]
    repo.create_user(str(uuid.uuid4()), "carol@example.com", "secret_pass")

    user = repo.verify_user_password("carol@example.com", "secret_pass")
    assert user is not None

    user = repo.verify_user_password("carol@example.com", "wrong_pass")
    assert user is None

    user = repo.verify_user_password("nonexistent@example.com", "pass")
    assert user is None


# ------------------------------------------------------------------
# Session management
# ------------------------------------------------------------------

def test_create_and_get_session(env):
    repo = env["repo"]
    user_id = str(uuid.uuid4())
    repo.create_user(user_id, "dave@example.com", "pass")

    token = "session_token_abc123"
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()
    session = repo.create_session(
        session_id=str(uuid.uuid4()),
        user_id=user_id,
        token=token,
        expires_at=expires_at,
    )
    assert session is not None
    assert session["user_id"] == user_id

    fetched = repo.get_session_by_token(token)
    assert fetched is not None
    assert fetched["user_id"] == user_id

    assert repo.get_session_by_token("nonexistent_token") is None


def test_delete_session(env):
    repo = env["repo"]
    user_id = str(uuid.uuid4())
    repo.create_user(user_id, "erin@example.com", "pass")

    token = "token_to_delete"
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()
    repo.create_session(str(uuid.uuid4()), user_id, token, expires_at)

    assert repo.get_session_by_token(token) is not None
    assert repo.delete_session(token) is True
    assert repo.get_session_by_token(token) is None
    assert repo.delete_session(token) is False  # already deleted


def test_session_expired(env):
    repo = env["repo"]
    user_id = str(uuid.uuid4())
    repo.create_user(user_id, "frank@example.com", "pass")

    token = "expired_token"
    expires_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()  # already expired
    repo.create_session(str(uuid.uuid4()), user_id, token, expires_at)

    session = repo.get_session_by_token(token)
    assert session is not None  # exists in DB
    # But is_expired check would catch it
    expires_at_parsed = datetime.fromisoformat(session["expires_at"])
    assert expires_at_parsed <= datetime.now(timezone.utc)


def test_cleanup_expired_sessions(env):
    repo = env["repo"]
    user_id = str(uuid.uuid4())
    repo.create_user(user_id, "grace@example.com", "pass")

    # Create one expired and one valid session
    expired_token = "expired_cleanup"
    valid_token = "valid_cleanup"
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()

    repo.create_session(str(uuid.uuid4()), user_id, expired_token, past)
    repo.create_session(str(uuid.uuid4()), user_id, valid_token, future)

    cleaned = repo.cleanup_expired_sessions()
    assert cleaned == 1
    assert repo.get_session_by_token(expired_token) is None
    assert repo.get_session_by_token(valid_token) is not None


# ------------------------------------------------------------------
# Auth enabled/disabled behavior
# ------------------------------------------------------------------

def test_auth_disabled_by_default(env):
    os.environ["AUTH_ENABLED"] = "false"
    assert auth.AUTH_ENABLED is False

    # require_auth should return dev user when disabled
    import asyncio
    user = asyncio.get_event_loop().run_until_complete(auth.require_auth(type("Request", (), {"cookies": {}, "url": type("URL", (), {"path": "/api/test"})()})()))
    assert user["id"] == "dev"


def test_is_public_path():
    assert auth._is_public_path("/api/health") is True
    assert auth._is_public_path("/api/sample-pdf-available") is True
    assert auth._is_public_path("/api/auth/login") is True
    assert auth._is_public_path("/static/css/style.css") is True
    assert auth._is_public_path("/api/projects/123") is False
    assert auth._is_public_path("/api/projects/123/images") is False


# ==================================================================
# Phase C Security Remediation Regression Suite
# ==================================================================

import io
import fitz
from PIL import Image
from fastapi.testclient import TestClient
from src.web.app import create_app, parse_cors_origins
from src.storage.filestore import FileStore

STRONG_TEST_SECRET = "v9K3mP8xQ2wL7nJ4rT6yU1zB5cV0fG9hJ2kL5mN8pQ1rS4tV"


def _make_sample_pdf_bytes() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=400, height=400)
    img = Image.new("RGB", (80, 80), color="navy")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    page.insert_image(fitz.Rect(50, 50, 150, 150), stream=buf.getvalue())
    page.insert_text(fitz.Point(50, 175), "Figure 1: Security test sample image.", fontsize=10)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


@pytest.fixture
def auth_app_env(tmp_path, monkeypatch):
    """Provides a FastAPI TestClient with AUTH_ENABLED=true and a strong SESSION_SECRET_KEY."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SESSION_SECRET_KEY", STRONG_TEST_SECRET)
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://app.example.com,http://localhost:8000")
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "_INITIAL_AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "SESSION_SECRET_KEY", STRONG_TEST_SECRET)
    auth.reset_rate_limit_state()

    db_file = str(tmp_path / "sec_test.db")
    storage_dir = str(tmp_path / "sec_storage")
    app = create_app(db_path=db_file, storage_dir=storage_dir)
    repo = app.state.repo
    filestore = app.state.filestore

    user_a = repo.create_user(str(uuid.uuid4()), "alice@example.com", "AliceStrongPass!123")
    user_b = repo.create_user(str(uuid.uuid4()), "bob@example.com", "BobStrongPass!456")

    yield {
        "app": app,
        "repo": repo,
        "filestore": filestore,
        "user_a": user_a,
        "user_b": user_b,
        "tmp_path": tmp_path,
    }

    auth.reset_rate_limit_state()
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setattr(auth, "AUTH_ENABLED", False)
    monkeypatch.setattr(auth, "_INITIAL_AUTH_ENABLED", False)


def _login_client(app, email: str, password: str) -> tuple[TestClient, str]:
    """Logs in a fresh TestClient and returns (client, csrf_token)."""
    client = TestClient(app)
    csrf_resp = client.get("/api/auth/csrf-token")
    assert csrf_resp.status_code == 200
    pre_csrf = csrf_resp.json()["csrf_token"]

    login_resp = client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
        headers={"X-CSRF-Token": pre_csrf},
    )
    assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
    csrf_token = login_resp.json()["csrf_token"]
    return client, csrf_token


# ------------------------------------------------------------------
# 1. Startup Configuration & Secret Strength Tests
# ------------------------------------------------------------------

def test_startup_fails_when_auth_enabled_and_secret_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.delenv("SESSION_SECRET_KEY", raising=False)
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "_INITIAL_AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "SESSION_SECRET_KEY", "")

    with pytest.raises(RuntimeError, match="SESSION_SECRET_KEY is required"):
        create_app(db_path=str(tmp_path / "fail1.db"), storage_dir=str(tmp_path / "s1"))


@pytest.mark.parametrize(
    "weak_secret",
    [
        "",
        "   ",
        "short_secret_123",
        "changeme",
        "secret",
        "placeholder_key_that_is_over_thirty_two_chars_long",
        "your-secret-key-must-be-replaced-in-production-1234",
        "a" * 40,
        "1234567" * 6,
    ],
)
def test_startup_rejects_weak_or_placeholder_secrets(tmp_path, monkeypatch, weak_secret):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SESSION_SECRET_KEY", weak_secret)
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "_INITIAL_AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "SESSION_SECRET_KEY", weak_secret)

    with pytest.raises(RuntimeError):
        create_app(db_path=str(tmp_path / "fail2.db"), storage_dir=str(tmp_path / "s2"))


def test_production_startup_refuses_unauthenticated_fallback(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setattr(auth, "AUTH_ENABLED", False)
    monkeypatch.setattr(auth, "_INITIAL_AUTH_ENABLED", False)

    with pytest.raises(RuntimeError, match="AUTH_ENABLED must be set to 'true'"):
        auth.validate_auth_config(require_auth_enabled=True)


# ------------------------------------------------------------------
# 2. Session Security, Fixation, Expiry, Tampering, Inactive User, Rate Limit
# ------------------------------------------------------------------

def test_unauthenticated_requests_to_protected_routes_return_401(auth_app_env):
    app = auth_app_env["app"]
    repo = auth_app_env["repo"]
    user_a = auth_app_env["user_a"]

    proj_id = str(uuid.uuid4())
    repo.create_project(proj_id, "Private Proj", "a.pdf", "source/a.pdf", "chk", owner_id=user_a["id"])

    anon = TestClient(app)
    assert anon.get("/api/projects").status_code == 401
    assert anon.get(f"/api/projects/{proj_id}").status_code == 401
    assert anon.get(f"/api/projects/{proj_id}/images").status_code == 401
    assert anon.get(f"/review/{proj_id}").status_code == 401
    assert anon.get(f"/export/{proj_id}").status_code == 401
    assert anon.get("/api/ai/config").status_code == 401
    assert anon.get("/api/ai/usage").status_code == 401

    # Public endpoints remain accessible
    assert anon.get("/api/health").status_code == 200
    assert anon.get("/").status_code == 200
    assert anon.get("/static/api-client.js").status_code == 200


def test_valid_login_sets_secure_cookies_and_hides_session_token(auth_app_env, monkeypatch):
    monkeypatch.setenv("COOKIE_SECURE", "true")
    app = auth_app_env["app"]
    client = TestClient(app, base_url="https://testserver")

    csrf_resp = client.get("/api/auth/csrf-token")
    pre_csrf = csrf_resp.json()["csrf_token"]

    resp = client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "AliceStrongPass!123"},
        headers={"X-CSRF-Token": pre_csrf},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "logged_in"
    assert body["user"]["email"] == "alice@example.com"
    assert "token" not in body
    assert "session_token" not in body
    assert "alttext_session" not in body

    set_cookies = resp.headers.get_list("set-cookie")
    session_cookie_hdr = [c for c in set_cookies if "alttext_session=" in c][0]
    assert "HttpOnly" in session_cookie_hdr
    assert "SameSite=lax" in session_cookie_hdr
    assert "Path=/" in session_cookie_hdr
    assert "Secure" in session_cookie_hdr

    sess_resp = client.get("/api/auth/session")
    assert sess_resp.status_code == 200
    assert sess_resp.json()["user"]["email"] == "alice@example.com"


def test_tampered_session_cookie_returns_401(auth_app_env):
    app = auth_app_env["app"]
    client, _ = _login_client(app, "alice@example.com", "AliceStrongPass!123")

    # Tamper with cookie signature
    raw_cookie = client.cookies.get(auth.SESSION_COOKIE_NAME)
    tampered = raw_cookie[:-4] + ("0000" if not raw_cookie.endswith("0000") else "1111")
    client.cookies.clear()
    client.cookies.set(auth.SESSION_COOKIE_NAME, tampered)

    assert client.get("/api/projects").status_code == 401
    assert client.get("/api/auth/session").status_code == 401


def test_expired_session_rejected_and_revoked(auth_app_env):
    app = auth_app_env["app"]
    repo = auth_app_env["repo"]
    user_a = auth_app_env["user_a"]

    raw_token = auth._make_session_token()
    expired_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    repo.create_session(str(uuid.uuid4()), user_a["id"], raw_token, expired_at)

    client = TestClient(app)
    client.cookies.set(auth.SESSION_COOKIE_NAME, auth._sign(raw_token))

    assert client.get("/api/projects").status_code == 401
    # Expired session should be cleaned up from DB on access
    assert repo.get_session_by_token(raw_token) is None


def test_inactive_user_rejected_at_login_and_session_validation(auth_app_env):
    app = auth_app_env["app"]
    repo = auth_app_env["repo"]
    user_a = auth_app_env["user_a"]

    client, _ = _login_client(app, "alice@example.com", "AliceStrongPass!123")
    assert client.get("/api/projects").status_code == 200

    # Deactivate user while session is active
    repo.set_user_active(user_a["id"], False)

    # Existing session must now return 401
    assert client.get("/api/projects").status_code == 401

    # Fresh login attempt for inactive user must also return 401
    anon = TestClient(app)
    pre_csrf = anon.get("/api/auth/csrf-token").json()["csrf_token"]
    login_resp = anon.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "AliceStrongPass!123"},
        headers={"X-CSRF-Token": pre_csrf},
    )
    assert login_resp.status_code == 401


def test_session_fixation_rotates_and_invalidates_prior_session(auth_app_env):
    app = auth_app_env["app"]
    repo = auth_app_env["repo"]
    user_a = auth_app_env["user_a"]

    prior_token = auth._make_session_token()
    future = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
    repo.create_session(str(uuid.uuid4()), user_a["id"], prior_token, future)

    client = TestClient(app)
    client.cookies.set(auth.SESSION_COOKIE_NAME, auth._sign(prior_token), domain="testserver.local")
    csrf_resp = client.get("/api/auth/csrf-token")
    csrf = csrf_resp.json()["csrf_token"]

    login_resp = client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "AliceStrongPass!123"},
        headers={"X-CSRF-Token": csrf},
    )
    assert login_resp.status_code == 200

    # Old session token must be invalidated in DB
    assert repo.get_session_by_token(prior_token) is None
    # New session cookie must be different
    assert client.cookies.get(auth.SESSION_COOKIE_NAME, domain="testserver.local") != auth._sign(prior_token)


def test_logout_invalidates_session_and_repeated_logout_is_safe(auth_app_env):
    app = auth_app_env["app"]
    client, csrf_token = _login_client(app, "alice@example.com", "AliceStrongPass!123")

    # Logout without CSRF must fail with 403
    assert client.post("/api/auth/logout").status_code == 403

    # Valid logout succeeds
    out1 = client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf_token})
    assert out1.status_code == 200
    assert out1.json()["status"] == "logged_out"

    # Session is now invalid
    assert client.get("/api/projects").status_code == 401

    # Repeated logout with fresh pre-auth CSRF token is safe and idempotent
    fresh_csrf = client.get("/api/auth/csrf-token").json()["csrf_token"]
    out2 = client.post("/api/auth/logout", headers={"X-CSRF-Token": fresh_csrf})
    assert out2.status_code == 200


def test_login_rate_limiting_and_password_length_guard(auth_app_env):
    app = auth_app_env["app"]
    client = TestClient(app)
    csrf = client.get("/api/auth/csrf-token").json()["csrf_token"]

    # Overly long password rejected with 400 before hashing
    long_resp = client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "x" * 300},
        headers={"X-CSRF-Token": csrf},
    )
    assert long_resp.status_code == 400

    # 5 failed attempts return 401, 6th returns 429 Too Many Requests
    for _ in range(5):
        r = client.post(
            "/api/auth/login",
            json={"email": "alice@example.com", "password": "WrongPassword!"},
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 401

    throttled = client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "AliceStrongPass!123"},
        headers={"X-CSRF-Token": csrf},
    )
    assert throttled.status_code == 429


# ------------------------------------------------------------------
# 3. CSRF Enforcement Across All State-Changing Endpoints
# ------------------------------------------------------------------

def test_csrf_enforced_across_all_state_changing_endpoints(auth_app_env):
    app = auth_app_env["app"]
    repo = auth_app_env["repo"]
    user_a = auth_app_env["user_a"]

    proj_id = str(uuid.uuid4())
    img_id = str(uuid.uuid4())
    grp_id = str(uuid.uuid4())
    repo.create_project(proj_id, "CSRF Test", "t.pdf", "source/t.pdf", "chk", owner_id=user_a["id"])
    repo.create_figure_group(grp_id, proj_id, "Group cap", 1, "below", "Fig 1", False, "candidate", [])
    repo.create_image_asset(
        image_id=img_id,
        project_id=proj_id,
        page_number=1,
        xref=10,
        placement_index=1,
        bbox={"x0": 0, "y0": 0, "x1": 50, "y1": 50},
        original_caption="Cap",
        caption_position="below",
        needs_caption_review=False,
        review_reasons=[],
        suggested_filename="Fig1.png",
        file_storage_key="images/Fig1.png",
        file_checksum="chk",
        image_format="png",
    )

    client, valid_csrf = _login_client(app, "alice@example.com", "AliceStrongPass!123")

    state_changing_calls = [
        ("POST", "/api/auth/logout", None),
        ("POST", "/api/upload-and-extract", None),
        ("POST", f"/api/projects/{proj_id}/images/{img_id}/generate", {}),
        ("POST", f"/api/projects/{proj_id}/images/{img_id}/draft", {"text": "Draft text"}),
        ("POST", f"/api/projects/{proj_id}/images/{img_id}/clean-draft", {"text": "Draft text"}),
        ("POST", f"/api/projects/{proj_id}/images/{img_id}/approve", {"alt_text": "Approved text"}),
        ("PUT", f"/api/projects/{proj_id}/images/{img_id}/caption", {"caption": "Updated"}),
        ("PUT", f"/api/projects/{proj_id}/images/{img_id}/association", {"association_type": "single"}),
        (
            "POST",
            f"/api/projects/{proj_id}/groups/create-from-selection",
            {"image_ids": [img_id], "caption": "Cap", "decision_mode": "custom"},
        ),
        ("POST", f"/api/projects/{proj_id}/groups/{grp_id}/confirm", None),
        ("POST", f"/api/projects/{proj_id}/groups/{grp_id}/add-image", {"image_id": img_id}),
        ("POST", f"/api/projects/{proj_id}/groups/{grp_id}/remove-image", {"image_id": img_id}),
        ("POST", f"/api/projects/{proj_id}/groups/{grp_id}/split", None),
        ("POST", f"/api/projects/{proj_id}/exports/docx", None),
        ("DELETE", f"/api/projects/{proj_id}", None),
    ]

    for method, path, payload in state_changing_calls:
        # 1. Missing X-CSRF-Token header -> 403
        resp_missing = client.request(method, path, json=payload) if payload is not None else client.request(method, path)
        assert resp_missing.status_code == 403, f"Expected 403 for {method} {path} without CSRF, got {resp_missing.status_code}"

        # 2. Invalid X-CSRF-Token header -> 403
        resp_invalid = client.request(
            method,
            path,
            json=payload,
            headers={"X-CSRF-Token": "invalid.token"},
        ) if payload is not None else client.request(
            method,
            path,
            headers={"X-CSRF-Token": "invalid.token"},
        )
        assert resp_invalid.status_code == 403, f"Expected 403 for {method} {path} with invalid CSRF"

        # 3. CSRF token passed ONLY in URL query parameter -> 403
        query_path = f"{path}?csrf_token={valid_csrf}"
        resp_query = client.request(method, query_path, json=payload) if payload is not None else client.request(method, query_path)
        assert resp_query.status_code == 403, f"Expected 403 for {method} {query_path} with query-param CSRF"


def test_cross_session_csrf_token_reuse_rejected(auth_app_env):
    app = auth_app_env["app"]
    repo = auth_app_env["repo"]
    user_a = auth_app_env["user_a"]

    proj_id = str(uuid.uuid4())
    img_id = str(uuid.uuid4())
    repo.create_project(proj_id, "Alice Proj", "a.pdf", "source/a.pdf", "chk", owner_id=user_a["id"])
    repo.create_image_asset(
        image_id=img_id,
        project_id=proj_id,
        page_number=1,
        xref=10,
        placement_index=1,
        bbox={"x0": 0, "y0": 0, "x1": 50, "y1": 50},
        original_caption="Cap",
        caption_position="below",
        needs_caption_review=False,
        review_reasons=[],
        suggested_filename="Fig1.png",
        file_storage_key="images/Fig1.png",
        file_checksum="chk",
        image_format="png",
    )

    client_a, csrf_a = _login_client(app, "alice@example.com", "AliceStrongPass!123")
    _, csrf_b = _login_client(app, "bob@example.com", "BobStrongPass!456")

    # Stuff Bob's CSRF token into Alice's cookie and header -> must fail with 403
    client_a.cookies.set(auth.CSRF_TOKEN_NAME, csrf_b, domain="testserver.local")
    cross_resp = client_a.post(
        f"/api/projects/{proj_id}/images/{img_id}/draft",
        json={"text": "Cross session attempt"},
        headers={"X-CSRF-Token": csrf_b},
    )
    assert cross_resp.status_code == 403

    # Restore Alice's valid CSRF token -> succeeds with 200
    client_a.cookies.set(auth.CSRF_TOKEN_NAME, csrf_a, domain="testserver.local")
    ok_resp = client_a.post(
        f"/api/projects/{proj_id}/images/{img_id}/draft",
        json={"text": "Valid draft edit"},
        headers={"X-CSRF-Token": csrf_a},
    )
    assert ok_resp.status_code == 200


# ------------------------------------------------------------------
# 4. CORS Configuration & Preflight Tests
# ------------------------------------------------------------------

def test_cors_origin_parsing_and_wildcard_rejection():
    with pytest.raises(ValueError, match="wildcard"):
        parse_cors_origins("*")

    with pytest.raises(ValueError, match="wildcard"):
        parse_cors_origins("https://app.example.com, https://*.example.com")

    with pytest.raises(ValueError, match="Invalid CORS origin syntax"):
        parse_cors_origins("not-a-url")

    with pytest.raises(ValueError, match="Invalid CORS origin syntax"):
        parse_cors_origins("https://app.example.com/some/path")

    parsed = parse_cors_origins("https://app.example.com/, http://localhost:8000")
    assert parsed == ["https://app.example.com", "http://localhost:8000"]


def test_cors_approved_vs_rejected_origins_and_preflight(auth_app_env):
    app = auth_app_env["app"]
    client = TestClient(app)

    # Approved origin preflight OPTIONS with X-CSRF-Token
    preflight_ok = client.options(
        "/api/upload-and-extract",
        headers={
            "Origin": "https://app.example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type, X-CSRF-Token",
        },
    )
    assert preflight_ok.status_code == 200
    assert preflight_ok.headers.get("access-control-allow-origin") == "https://app.example.com"
    assert preflight_ok.headers.get("access-control-allow-credentials") == "true"
    allowed_hdrs = preflight_ok.headers.get("access-control-allow-headers", "").lower()
    assert "x-csrf-token" in allowed_hdrs
    assert "content-type" in allowed_hdrs

    # Disallowed origin does not get Access-Control-Allow-Origin
    preflight_bad = client.options(
        "/api/upload-and-extract",
        headers={
            "Origin": "https://evil.example.org",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type, X-CSRF-Token",
        },
    )
    assert "access-control-allow-origin" not in preflight_bad.headers


# ------------------------------------------------------------------
# 5. Authorization & Multi-User Project Isolation Tests
# ------------------------------------------------------------------

def test_user_a_vs_user_b_project_isolation_across_all_resources(auth_app_env):
    app = auth_app_env["app"]
    repo = auth_app_env["repo"]

    client_a, csrf_a = _login_client(app, "alice@example.com", "AliceStrongPass!123")
    client_b, csrf_b = _login_client(app, "bob@example.com", "BobStrongPass!456")

    # Alice uploads a PDF and creates a project
    pdf_bytes = _make_sample_pdf_bytes()
    upload_resp = client_a.post(
        "/api/upload-and-extract",
        files={"file": ("alice_doc.pdf", pdf_bytes, "application/pdf")},
        data={"project_name": "Alice Secret Book"},
        headers={"X-CSRF-Token": csrf_a},
    )
    assert upload_resp.status_code == 200
    proj_id = upload_resp.json()["project_id"]
    images = upload_resp.json()["images"]
    assert len(images) >= 1
    img_id = images[0]["id"]

    # Alice saves and approves a draft (attempting to spoof reviewer_id as 'spoofed_admin')
    approve_resp = client_a.post(
        f"/api/projects/{proj_id}/images/{img_id}/approve",
        json={"alt_text": "Detailed chart showing Etruscan bronze mirror engravings.", "reviewer_id": "spoofed_admin"},
        headers={"X-CSRF-Token": csrf_a},
    )
    assert approve_resp.status_code == 200
    # Server must ignore browser-supplied reviewer_id and record Alice's identity
    assert approve_resp.json()["approval"]["reviewer_id"] == "alice@example.com"

    # Alice exports DOCX
    exp_resp = client_a.post(
        f"/api/projects/{proj_id}/exports/docx",
        headers={"X-CSRF-Token": csrf_a},
    )
    assert exp_resp.status_code == 200
    export_id = exp_resp.json()["export_id"]

    # Alice can list, view, review, and download her own project and export
    list_a = client_a.get("/api/projects").json()["projects"]
    assert any(p["id"] == proj_id for p in list_a)
    assert client_a.get(f"/api/projects/{proj_id}").status_code == 200
    assert client_a.get(f"/review/{proj_id}").status_code == 200
    assert client_a.get(f"/export/{proj_id}").status_code == 200
    assert client_a.get(f"/api/projects/{proj_id}/images/{img_id}/file").status_code == 200
    assert client_a.get(f"/api/projects/{proj_id}/exports/{export_id}/download").status_code == 200

    # Bob's project list must NOT include Alice's project
    list_b = client_b.get("/api/projects").json()["projects"]
    assert not any(p["id"] == proj_id for p in list_b)

    # Bob is forbidden (403) from accessing any of Alice's pages, images, actions, exports, or deletion
    assert client_b.get(f"/review/{proj_id}").status_code == 403
    assert client_b.get(f"/export/{proj_id}").status_code == 403
    assert client_b.get(f"/api/projects/{proj_id}").status_code == 403
    assert client_b.get(f"/api/projects/{proj_id}/images").status_code == 403
    assert client_b.get(f"/api/projects/{proj_id}/images/{img_id}/file").status_code == 403
    assert client_b.get(f"/api/projects/{proj_id}/images/{img_id}/versions").status_code == 403
    assert client_b.get(f"/api/projects/{proj_id}/groups").status_code == 403
    assert client_b.get(f"/api/projects/{proj_id}/exports/status").status_code == 403
    assert client_b.get(f"/api/projects/{proj_id}/exports/{export_id}/download").status_code == 403

    assert client_b.post(
        f"/api/projects/{proj_id}/images/{img_id}/generate",
        json={},
        headers={"X-CSRF-Token": csrf_b},
    ).status_code == 403
    assert client_b.post(
        f"/api/projects/{proj_id}/images/{img_id}/draft",
        json={"text": "Bob tampering"},
        headers={"X-CSRF-Token": csrf_b},
    ).status_code == 403
    assert client_b.post(
        f"/api/projects/{proj_id}/images/{img_id}/approve",
        json={"alt_text": "Bob approval"},
        headers={"X-CSRF-Token": csrf_b},
    ).status_code == 403
    assert client_b.put(
        f"/api/projects/{proj_id}/images/{img_id}/caption",
        json={"caption": "Bob caption"},
        headers={"X-CSRF-Token": csrf_b},
    ).status_code == 403
    assert client_b.post(
        f"/api/projects/{proj_id}/exports/docx",
        headers={"X-CSRF-Token": csrf_b},
    ).status_code == 403
    assert client_b.delete(
        f"/api/projects/{proj_id}",
        headers={"X-CSRF-Token": csrf_b},
    ).status_code == 403

    # Active job prevents cleanup even for owner Alice (returns 409)
    job = repo.create_job(str(uuid.uuid4()), proj_id, "extraction_chunk", page_start=1, page_end=5)
    repo.update_job_status(job["id"], "running")
    del_blocked = client_a.delete(
        f"/api/projects/{proj_id}",
        headers={"X-CSRF-Token": csrf_a},
    )
    assert del_blocked.status_code == 409

    # Complete job -> Alice can now delete her project
    repo.update_job_status(job["id"], "completed")
    del_ok = client_a.delete(
        f"/api/projects/{proj_id}",
        headers={"X-CSRF-Token": csrf_a},
    )
    assert del_ok.status_code == 200
    assert repo.get_project(proj_id) is None
