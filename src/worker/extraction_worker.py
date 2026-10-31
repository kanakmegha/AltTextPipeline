"""Crash-recoverable extraction job execution shared by Render and tests."""

import hashlib
import logging
import os
import tempfile
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from datetime import datetime, timedelta, timezone

import pymupdf as fitz
from src.extraction.engine import ExtractionEngine

logger = logging.getLogger("alttext.worker")
LEASE_SECONDS = 300


def _env_int(name, default, minimum=1, maximum=64):
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def _extract_pdf_batch(pdf_path, page_numbers):
    """Process one batch with a fresh PyMuPDF document handle in the child process."""
    return ExtractionEngine().process_pdf(pdf_path, page_numbers=page_numbers, group_results=False)


def _process_batch(pdf_path, pages, engine):
    if engine is None:
        return _extract_pdf_batch(pdf_path, pages)
    return engine.process_pdf(pdf_path, page_numbers=pages, group_results=False)


def _persist_batch_images(repo, filestore, project_id, result):
    context = result.grouping_context or {}
    images_by_page = context.get("images_by_page", {})
    for page in sorted(images_by_page):
        for image in images_by_page[page]:
            if not repo.find_image_asset_by_source(project_id, image.page_number, image.xref, image.placement_index):
                image_key, image_checksum, _ = filestore.save_image(
                    project_id=project_id, image_id=image.image_id,
                    image_bytes=image.image_bytes, ext=image.image_format,
                )
                repo.create_image_asset(
                    image_id=image.image_id, project_id=project_id, page_number=image.page_number,
                    xref=image.xref, placement_index=image.placement_index, bbox=image.bbox.to_dict(),
                    original_caption=image.caption, caption_position=image.caption_position,
                    needs_caption_review=image.needs_caption_review, review_reasons=image.review_reasons,
                    suggested_filename=image.suggested_filename, file_storage_key=image_key,
                    file_checksum=image_checksum, image_format=image.image_format,
                    processing_status="ready_for_review", group_id=None,
                    association_type=image.association_type,
                    association_confidence=image.association_confidence, panel_label=image.panel_label,
                )
            # Keep grouping metadata in memory but release image payloads as soon as they are persisted.
            image.image_bytes = b""
    return context


def _finalize_document_grouping(repo, project_id, engine, contexts):
    images_by_page, text_blocks_by_page, page_dimensions = {}, {}, {}
    for context in contexts:
        images_by_page.update(context.get("images_by_page", {}))
        text_blocks_by_page.update(context.get("text_blocks_by_page", {}))
        page_dimensions.update(context.get("page_dimensions", {}))
    final_images, groups = engine.group_extracted_pages(images_by_page, text_blocks_by_page, page_dimensions)
    group_ids = {}
    for group in groups:
        stable_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
            f"{project_id}:{group.caption_page_number}:{group.caption_text}:{group.figure_label or ''}"))
        group_ids[group.group_id] = stable_id
        if not repo.get_figure_group(stable_id):
            repo.create_figure_group(
                group_id=stable_id, project_id=project_id, caption_text=group.caption_text,
                caption_page_number=group.caption_page_number,
                caption_position=group.caption_position or "below", figure_label=group.figure_label,
                is_cross_page=group.is_cross_page, review_status=group.review_status,
                review_reasons=group.review_reasons,
            )
    for image in final_images:
        existing = repo.find_image_asset_by_source(project_id, image.page_number, image.xref, image.placement_index)
        if not existing:
            continue
        image.group_id = group_ids.get(image.group_id, image.group_id)
        repo.update_image_group_association(
            existing["id"], image.group_id, association_type=image.association_type,
            association_confidence=image.association_confidence, panel_label=image.panel_label,
            caption=image.caption, caption_position=image.caption_position,
            needs_caption_review=image.needs_caption_review, review_reasons=image.review_reasons,
        )


def process_extraction_job(job_id, repo, filestore, engine=None, heartbeat_seconds=30, worker_id=None):
    """Claim once in SQL, run extraction, persist idempotently, and record outcome."""
    now = datetime.now(timezone.utc)
    worker_id = worker_id or f"worker-{os.getpid()}-{uuid.uuid4()}"
    max_retries = _env_int("MAX_RETRIES", 3)
    claimed = repo.claim_job(
        job_id, _iso(now - timedelta(seconds=LEASE_SECONDS)), _iso(now),
        worker_id=worker_id, max_attempts=max_retries,
    )
    if not claimed:
        return False
    project_id = claimed["project_id"]
    project = repo.get_project(project_id)
    if not project:
        repo.update_job_status(job_id, "failed", error_message="Project not found")
        return False

    stopped = threading.Event()
    lease_lost = threading.Event()

    def heartbeat():
        while not stopped.wait(heartbeat_seconds):
            try:
                if not repo.renew_job_lease(job_id, worker_id, _iso(datetime.now(timezone.utc))):
                    lease_lost.set()
                    logger.warning("Worker %s lost lease for extraction job %s", worker_id, job_id)
                    return
            except Exception:
                logger.exception("Could not renew extraction job lease %s", job_id)

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    tmp_path = None
    try:
        repo.update_project_status(project_id, status="extracting")
        source = filestore.read_bytes(project_id, project["source_storage_key"])
        if not source.startswith(b"%PDF-"):
            raise ValueError("Stored source is not a PDF")
        repo.update_project_source_checksum(project_id, hashlib.sha256(source).hexdigest())
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(source)
            tmp_path = tmp.name
        del source
        doc = fitz.open(tmp_path)
        total_document_pages = len(doc)
        doc.close()
        page_numbers = claimed.get("page_numbers")
        if not page_numbers and claimed.get("page_start") and claimed.get("page_end"):
            page_numbers = list(range(int(claimed["page_start"]), int(claimed["page_end"]) + 1))
        if not page_numbers:
            page_numbers = list(range(1, total_document_pages + 1))
        page_numbers = sorted({int(page) for page in page_numbers if 1 <= int(page) <= total_document_pages})
        if not page_numbers:
            raise ValueError("Extraction job has no valid pages to process")

        batch_size = _env_int("PDF_BATCH_SIZE", 5)
        max_parallel = _env_int("MAX_PARALLEL_BATCHES", 2)
        page_batches = [page_numbers[i:i + batch_size] for i in range(0, len(page_numbers), batch_size)]
        repo.initialize_job_batches(job_id, page_batches, total_document_pages, max_attempts=max_retries)
        batch_rows = {row["batch_index"]: row for row in repo.list_job_batches(job_id)}
        contexts = []
        active_engine = engine or ExtractionEngine()
        use_processes = engine is None and max_parallel > 1 and len(page_batches) > 1
        if use_processes:
            _process_batches_parallel(
                job_id, project_id, tmp_path, repo, filestore, page_batches,
                batch_rows, contexts, active_engine, max_parallel, worker_id, lease_lost,
            )
        else:
            for index, pages in enumerate(page_batches):
                if lease_lost.is_set() or not repo.renew_job_lease(
                    job_id, worker_id, _iso(datetime.now(timezone.utc))
                ):
                    raise RuntimeError("Worker lost the extraction job lease")
                batch = batch_rows[index]
                if batch["status"] != "completed" and not repo.start_job_batch(
                    job_id, index, _iso(datetime.now(timezone.utc)), worker_id=worker_id
                ):
                    raise RuntimeError(f"Extraction batch {index + 1} exhausted its retry limit")
                result = _process_batch(tmp_path, pages, engine)
                if lease_lost.is_set() or not repo.renew_job_lease(
                    job_id, worker_id, _iso(datetime.now(timezone.utc))
                ):
                    raise RuntimeError("Worker lost the extraction job lease")
                contexts.append(_persist_batch_images(repo, filestore, project_id, result))
                if batch["status"] != "completed":
                    if result.errors:
                        repo.set_job_batch_status(
                            job_id, index, "failed", repr(result.errors[:3]), worker_id=worker_id
                        )
                        raise RuntimeError(f"Page batch {index + 1} reported extraction errors")
                    if not repo.set_job_batch_status(job_id, index, "completed", worker_id=worker_id):
                        raise RuntimeError("Worker lost ownership before recording batch completion")

        if lease_lost.is_set() or not repo.renew_job_lease(job_id, worker_id, _iso(datetime.now(timezone.utc))):
            raise RuntimeError("Worker lost the extraction job lease")
        _finalize_document_grouping(repo, project_id, active_engine, contexts)
        if not repo.update_claimed_job_status(job_id, worker_id, "completed"):
            raise RuntimeError("Worker lost ownership before completing the extraction job")
        repo.update_project_status(project_id, status="ready_for_review")
        return True
    except Exception as exc:
        logger.exception("Extraction job %s failed", job_id)
        current = repo.get_job(job_id) or claimed
        retry = int(current.get("attempt_count", 1)) < int(current.get("max_attempts", 3))
        for batch in repo.list_job_batches(job_id):
            if batch["status"] == "running":
                batch_retry = int(batch["attempt_count"]) < int(batch["max_attempts"])
                repo.set_job_batch_status(
                    job_id, batch["batch_index"], "queued" if batch_retry else "failed",
                    str(exc)[:1000], worker_id=worker_id,
                )
        if repo.update_claimed_job_status(
            job_id, worker_id, "queued" if retry else "failed", error_message=str(exc)[:1000]
        ):
            repo.update_project_status(project_id, status="queued" if retry else "failed")
        return False
    finally:
        stopped.set()
        thread.join(timeout=1)
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                logger.warning("Could not remove temporary worker PDF %s", tmp_path)


def _process_batches_parallel(job_id, project_id, pdf_path, repo, filestore, page_batches,
                              batch_rows, contexts, engine, max_parallel, worker_id, lease_lost):
    pending = iter(enumerate(page_batches))
    active = {}
    first_error = None
    with ProcessPoolExecutor(max_workers=max_parallel) as executor:
        while True:
            while first_error is None and len(active) < max_parallel:
                if lease_lost.is_set() or not repo.renew_job_lease(
                    job_id, worker_id, _iso(datetime.now(timezone.utc))
                ):
                    lease_lost.set()
                    first_error = RuntimeError("Worker lost the extraction job lease")
                    break
                try:
                    index, pages = next(pending)
                except StopIteration:
                    break
                batch = batch_rows[index]
                if batch["status"] != "completed" and not repo.start_job_batch(
                    job_id, index, _iso(datetime.now(timezone.utc)), worker_id=worker_id
                ):
                    first_error = RuntimeError(f"Extraction batch {index + 1} exhausted its retry limit")
                    break
                active[executor.submit(_extract_pdf_batch, pdf_path, pages)] = (index, batch)
            if not active:
                break
            done, _ = wait(active, return_when=FIRST_COMPLETED)
            for future in done:
                index, batch = active.pop(future)
                try:
                    result = future.result()
                    if lease_lost.is_set() or not repo.renew_job_lease(
                        job_id, worker_id, _iso(datetime.now(timezone.utc))
                    ):
                        lease_lost.set()
                        raise RuntimeError("Worker lost the extraction job lease")
                    contexts.append(_persist_batch_images(repo, filestore, project_id, result))
                    if result.errors:
                        raise RuntimeError(f"Page batch {index + 1} reported extraction errors: {result.errors[:3]}")
                    if batch["status"] != "completed":
                        if not repo.set_job_batch_status(job_id, index, "completed", worker_id=worker_id):
                            raise RuntimeError("Worker lost ownership before recording batch completion")
                except Exception as exc:
                    retry_batch = int(batch["attempt_count"]) + (0 if batch["status"] == "completed" else 1) < int(batch["max_attempts"])
                    if batch["status"] != "completed":
                        repo.set_job_batch_status(
                            job_id, index, "queued" if retry_batch else "failed", str(exc)[:1000],
                            worker_id=worker_id,
                        )
                    if first_error is None:
                        first_error = exc
        if first_error is not None:
            raise first_error


def _process_message(message_id, fields, repo, filestore):
    job_id = fields.get("job_id")
    if job_id:
        process_extraction_job(job_id, repo, filestore)


def _recover_database_jobs(repo, filestore):
    """Process queued and abandoned jobs using PostgreSQL as the durable queue."""
    now = datetime.now(timezone.utc)
    stale_before = _iso(now - timedelta(seconds=LEASE_SECONDS))
    repo.fail_exhausted_jobs(stale_before)
    for job in repo.list_recoverable_jobs(stale_before, limit=100):
        process_extraction_job(job["id"], repo, filestore)


def run_worker(repo, filestore, redis_url=None, poll_seconds=5, stop_event=None):
    """Poll durable database jobs; optionally consume Redis Streams as a wake-up path."""
    if not redis_url:
        logger.info("REDIS_URL is unset; running in PostgreSQL polling mode")
        while stop_event is None or not stop_event.is_set():
            try:
                _recover_database_jobs(repo, filestore)
            except Exception:
                logger.exception("Database polling iteration failed; will retry")
            if stop_event is not None:
                stop_event.wait(poll_seconds)
            else:
                time.sleep(poll_seconds)
        return

    from redis import Redis
    from redis.exceptions import ResponseError
    from src.worker.queueing import queue_settings

    stream, group = queue_settings()
    client = Redis.from_url(redis_url, decode_responses=True)
    group_ready = False
    try:
        consumer = os.environ.get("RENDER_INSTANCE_ID", f"worker-{os.getpid()}")
        while stop_event is None or not stop_event.is_set():
            _recover_database_jobs(repo, filestore)
            try:
                if not group_ready:
                    try:
                        client.xgroup_create(stream, group, id="0", mkstream=True)
                    except ResponseError as exc:
                        if "BUSYGROUP" not in str(exc):
                            raise
                    group_ready = True
                batches = client.xreadgroup(group, consumer, {stream: ">"}, count=10, block=int(poll_seconds * 1000))
                for _, messages in batches:
                    for message_id, fields in messages:
                        _process_message(message_id, fields, repo, filestore)
                        # Persisted job state is authoritative; ack only after handler returns.
                        client.xack(stream, group, message_id)
                # Redeliver abandoned stream entries; DB claims suppress duplicate work.
                pending = client.xautoclaim(stream, group, consumer, min_idle_time=LEASE_SECONDS * 1000, start_id="0-0", count=10)
                for message_id, fields in pending[1]:
                    _process_message(message_id, fields, repo, filestore)
                    client.xack(stream, group, message_id)
            except Exception:
                group_ready = False
                logger.exception("Redis Streams unavailable; queued database jobs will still be recovered")
                if stop_event is not None:
                    stop_event.wait(poll_seconds)
                else:
                    time.sleep(poll_seconds)
    finally:
        client.close()

