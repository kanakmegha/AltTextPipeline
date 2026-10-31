from src.worker import __main__ as worker_main


def test_worker_starts_in_postgres_polling_mode_without_redis(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://worker/db")
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "private-blob-token")
    monkeypatch.setenv("BLOB_STORE_ID", "shared-store")
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("WORKER_POLL_SECONDS", raising=False)

    calls = {}
    connection = object()
    repository = object()
    storage = object()
    monkeypatch.setattr(
        worker_main,
        "create_connection",
        lambda *args, **kwargs: (calls.update(connection=(args, kwargs)) or connection),
    )
    monkeypatch.setattr(
        worker_main,
        "PostgresRepository",
        lambda conn: (calls.update(repository_connection=conn) or repository),
    )
    monkeypatch.setattr(
        worker_main,
        "create_storage",
        lambda *args, **kwargs: (calls.update(storage=(args, kwargs)) or storage),
    )
    monkeypatch.setattr(worker_main, "run_worker", lambda *args, **kwargs: calls.update(worker=(args, kwargs)))
    monkeypatch.setattr(worker_main.signal, "signal", lambda *_args: None)

    worker_main.main()

    args, kwargs = calls["worker"]
    assert args == (repository, storage, None)
    assert kwargs["poll_seconds"] == 5
    assert calls["repository_connection"] is connection
    assert calls["storage"][0] == ("blob",)
    assert calls["storage"][1]["blob_token"] == "private-blob-token"
    assert calls["storage"][1]["blob_store_id"] == "shared-store"


def test_worker_poll_interval_can_be_configured(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://worker/db")
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "private-blob-token")
    monkeypatch.setenv("WORKER_POLL_SECONDS", "12")
    monkeypatch.delenv("REDIS_URL", raising=False)

    captured = {}
    monkeypatch.setattr(worker_main, "create_connection", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(worker_main, "PostgresRepository", lambda _conn: object())
    monkeypatch.setattr(worker_main, "create_storage", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(worker_main, "run_worker", lambda *args, **kwargs: captured.update(args=args, kwargs=kwargs))
    monkeypatch.setattr(worker_main.signal, "signal", lambda *_args: None)

    worker_main.main()

    assert captured["args"][2] is None
    assert captured["kwargs"]["poll_seconds"] == 12
