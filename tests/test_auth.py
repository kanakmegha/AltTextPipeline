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
