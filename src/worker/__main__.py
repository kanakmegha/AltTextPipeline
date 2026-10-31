"""Render worker entry point: python -m src.worker"""

import os
import signal
import threading

from src.db.connection_factory import create_connection
from src.db.postgres_repository import PostgresRepository
from src.storage.storage_factory import create_storage
from src.worker.extraction_worker import run_worker


def main():
    database_url = os.environ["DATABASE_URL"]
    redis_url = os.environ.get("REDIS_URL") or None
    token = os.environ["BLOB_READ_WRITE_TOKEN"]
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    conn = create_connection("postgres", database_url=database_url)
    repo = PostgresRepository(conn)
    storage = create_storage("blob", blob_token=token, blob_store_id=os.environ.get("BLOB_STORE_ID"))
    run_worker(
        repo,
        storage,
        redis_url,
        poll_seconds=float(os.environ.get("WORKER_POLL_SECONDS", "5")),
        stop_event=stop,
    )


if __name__ == "__main__":
    main()
