"""Crash-recoverable extraction job execution shared by Render and tests."""

import hashlib
import logging
import os
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from src.extraction.engine import ExtractionEngine

logger = logging.getLogger("alttext.worker")
LEASE_SECONDS = 300


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def process_extraction_job(job_id, repo, filestore, engine=None, heartbeat_seconds=30):
    """Claim once in SQL, run extraction, persist idempotently, and record outcome."""
    now = datetime.now(timezone.utc)
    claimed = repo.claim_job(job_id, _iso(now - timedelta(seconds=LEASE_SECONDS)), _iso(now))
    if not claimed:
        return False
    project_id = claimed["project_id"]
    project = repo.get_project(project_id)
    if not project:
        repo.update_job_status(job_id, "failed", error_message="Project not found")
        return False

    stopped = threading.Event()

    def heartbeat():
        while not stopped.wait(heartbeat_seconds):
            try:
                repo.update_job_status(job_id, "running")
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
        page_numbers = claimed.get("page_numbers")
        if not page_numbers and claimed.get("page_start") and claimed.get("page_end"):
            page_numbers = list(range(int(claimed["page_start"]), int(claimed["page_end"]) + 1))
        result = (engine or ExtractionEngine()).process_pdf(tmp_path, page_numbers=page_numbers)

        group_ids = {}
        for group in result.groups:
            stable_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
                f"{project_id}:{group.caption_page_number}:{group.caption_text}:{group.figure_label or ''}"))
            group_ids[group.group_id] = stable_id
            if repo.get_figure_group(stable_id):
                continue
            repo.create_figure_group(
                group_id=stable_id, project_id=project_id, caption_text=group.caption_text,
                caption_page_number=group.caption_page_number,
                caption_position=group.caption_position or "below", figure_label=group.figure_label,
                is_cross_page=group.is_cross_page, review_status=group.review_status,
                review_reasons=group.review_reasons,
            )

        for image in result.images:
            existing = repo.find_image_asset_by_source(project_id, image.page_number, image.xref, image.placement_index)
            if existing:
                continue
            image_key, image_checksum, _ = filestore.save_image(
                project_id=project_id, image_id=image.image_id,
                image_bytes=image.image_bytes, ext=image.image_format,
            )
            group_id = group_ids.get(image.group_id, image.group_id)
            repo.create_image_asset(
                image_id=image.image_id, project_id=project_id, page_number=image.page_number,
                xref=image.xref, placement_index=image.placement_index, bbox=image.bbox.to_dict(),
                original_caption=image.caption, caption_position=image.caption_position,
                needs_caption_review=image.needs_caption_review, review_reasons=image.review_reasons,
                suggested_filename=image.suggested_filename, file_storage_key=image_key,
                file_checksum=image_checksum, image_format=image.image_format,
                processing_status="ready_for_review", group_id=group_id,
                association_type=image.association_type,
                association_confidence=image.association_confidence, panel_label=image.panel_label,
            )
        repo.update_job_status(job_id, "completed")
        repo.update_project_status(project_id, status="ready_for_review")
        return True
    except Exception as exc:
        logger.exception("Extraction job %s failed", job_id)
        current = repo.get_job(job_id) or claimed
        retry = int(current.get("attempt_count", 1)) < int(current.get("max_attempts", 3))
        repo.update_job_status(job_id, "queued" if retry else "failed", error_message=str(exc)[:1000])
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


def _process_message(message_id, fields, repo, filestore):
    job_id = fields.get("job_id")
    if job_id:
        process_extraction_job(job_id, repo, filestore)


def run_worker(repo, filestore, redis_url, poll_seconds=2, stop_event=None):
    """Consume Redis Streams and reconcile queued DB jobs after lost publishes."""
    from redis import Redis
    from redis.exceptions import ResponseError
    from src.worker.queueing import queue_settings

    stream, group = queue_settings()
    client = Redis.from_url(redis_url, decode_responses=True)
    group_ready = False
    try:
        consumer = os.environ.get("RENDER_INSTANCE_ID", f"worker-{os.getpid()}")
        while stop_event is None or not stop_event.is_set():
            now = datetime.now(timezone.utc)
            stale_before = _iso(now - timedelta(seconds=LEASE_SECONDS))
            repo.fail_exhausted_jobs(stale_before)
            recoverable = repo.list_recoverable_jobs(stale_before, limit=100)
            for job in recoverable:
                process_extraction_job(job["id"], repo, filestore)
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

