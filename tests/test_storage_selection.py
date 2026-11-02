import pytest

from src.storage.blob_storage import BlobStorage
from src.storage.filestore import FileStore
from src.web import auth
from src.web.app import create_app


def _create_app(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "validate_auth_config", lambda: None)
    return create_app(
        db_path=str(tmp_path / "selection.db"),
        storage_dir=str(tmp_path / "storage"),
    )


def test_local_development_defaults_to_filesystem_storage_even_with_blob_token(tmp_path, monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "available-but-not-selected")
    monkeypatch.delenv("BLOB_STORE_ID", raising=False)

    app = _create_app(tmp_path, monkeypatch)

    assert isinstance(app.state.storage, FileStore)


@pytest.mark.parametrize(
    "environment",
    [
        {"VERCEL": "1"},
        {"STORAGE_BACKEND": "blob"},
    ],
)
def test_production_blob_selection_uses_shared_private_store_configuration(
    tmp_path, monkeypatch, environment
):
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "private-blob-rw-token")
    monkeypatch.setenv("BLOB_STORE_ID", "shared-private-store")

    app = _create_app(tmp_path, monkeypatch)

    assert isinstance(app.state.storage, BlobStorage)
    assert app.state.storage.blob_token == "private-blob-rw-token"
    assert app.state.storage.blob_store_id == "shared-private-store"


@pytest.mark.parametrize(
    "environment",
    [
        {"VERCEL": "1"},
        {"STORAGE_BACKEND": "blob"},
    ],
)
def test_blob_selection_fails_clearly_without_read_write_token(tmp_path, monkeypatch, environment):
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)

    with pytest.raises(ValueError, match="BLOB_READ_WRITE_TOKEN is required"):
        _create_app(tmp_path, monkeypatch)
