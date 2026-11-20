"""Crash-recoverable extraction job execution shared by Render and tests."""

import gc
import hashlib
import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, as_completed, wait
from datetime import datetime, timedelta, timezone

import pymupdf as fitz
from src.extraction.engine import ExtractionEngine

logger = logging.getLogger("alttext.worker")
LEASE_SECONDS = 300
DEFAULT_PDF_BATCH_SIZE = 25
PDF_BATCH_SIZE = DEFAULT_PDF_BATCH_SIZE


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


def _strip_ipc_image_bytes(result, temp_dir):
    """Clear in-memory image_bytes when images were written to temp_dir to avoid large IPC payloads."""
    if not temp_dir:
        return
    context = result.grouping_context or {}
    for page_imgs in context.get("images_by_page", {}).values():
        for img in page_imgs:
            if img.suggested_filename and os.path.isfile(os.path.join(temp_dir, img.suggested_filename)):
                img.image_bytes = b""
    for img in getattr(result, "images", []) or []:
        if img.suggested_filename and os.path.isfile(os.path.join(temp_dir, img.suggested_filename)):
            img.image_bytes = b""


def _split_pdf_batch_to_temp_file(source_pdf_path, page_numbers, dest_pdf_path):
    """Split selected 1-based page_numbers from source_pdf_path into a standalone temporary PDF."""
    src_doc = fitz.open(source_pdf_path)
    batch_doc = fitz.open()
    try:
        for pno in page_numbers:
            batch_doc.insert_pdf(src_doc, from_page=pno - 1, to_page=pno - 1)
        batch_doc.save(dest_pdf_path, garbage=3, deflate=True)
    finally:
        batch_doc.close()
        src_doc.close()


def _remap_batch_result_pages(result, page_numbers):
    """Map local 1..N page numbers from a split batch PDF back to original document page numbers."""
    page_map = {idx + 1: orig_pno for idx, orig_pno in enumerate(page_numbers)}
    for img in getattr(result, "images", []) or []:
        img.page_number = page_map.get(img.page_number, img.page_number)

    context = result.grouping_context or {}
    if context:
        remapped_imgs = {}
        for local_pno, imgs in context.get("images_by_page", {}).items():
            orig_pno = page_map.get(local_pno, local_pno)
            for img in imgs:
                img.page_number = orig_pno
            remapped_imgs[orig_pno] = imgs
        context["images_by_page"] = remapped_imgs

        remapped_blocks = {}
        for local_pno, blocks in context.get("text_blocks_by_page", {}).items():
            orig_pno = page_map.get(local_pno, local_pno)
            for blk in blocks:
                if hasattr(blk, "page_number"):
                    blk.page_number = orig_pno
            remapped_blocks[orig_pno] = blocks
        context["text_blocks_by_page"] = remapped_blocks

        context["page_dimensions"] = {
            page_map.get(local_pno, local_pno): dim
            for local_pno, dim in context.get("page_dimensions", {}).items()
        }

    for err in getattr(result, "errors", []) or []:
        if isinstance(err, dict) and "page" in err:
            err["page"] = page_map.get(err["page"], err["page"])


def _extract_pdf_batch(pdf_path, page_numbers):
    """Process one page batch directly from the read-only source PDF with a fresh PyMuPDF handle."""
    temp_dir = tempfile.mkdtemp(prefix="pdf_batch_")
    try:
        result = ExtractionEngine().process_pdf(
            pdf_path, page_numbers=page_numbers, output_images_dir=temp_dir, group_results=False
        )
        _strip_ipc_image_bytes(result, temp_dir)
        return result, temp_dir
    except Exception:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def _process_batch(pdf_path, pages, engine):
    if engine is None:
        return _extract_pdf_batch(pdf_path, pages)
    temp_dir = tempfile.mkdtemp(prefix="pdf_batch_")
    try:
        result = engine.process_pdf(
            pdf_path, page_numbers=pages, output_images_dir=temp_dir, group_results=False
        )
        return result, temp_dir
    except Exception:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def _persist_batch_images(repo, filestore, project_id, result, batch_temp_dir=None, metrics=None, known_source_map=None):
    context = result.grouping_context or {}
    images_by_page = context.get("images_by_page", {})

    t0_lookup = time.perf_counter()
    db_lookup_count = 0
    if known_source_map is not None:
        existing_map = known_source_map
    elif hasattr(repo, "list_image_asset_source_keys"):
        existing_map = repo.list_image_asset_source_keys(project_id)
        db_lookup_count = 1
    else:
        existing_map = None

    images_to_upload = []
    seen_keys = set()
    for page in sorted(images_by_page):
        for image in images_by_page[page]:
            src_key = (int(image.page_number), int(image.xref), int(image.placement_index))
            if src_key in seen_keys:
                continue
            seen_keys.add(src_key)
            if existing_map is not None:
                existing_id = existing_map.get(src_key)
                if existing_id:
                    image.image_id = existing_id
                    continue
            else:
                db_lookup_count += 1
                existing = repo.find_image_asset_by_source(project_id, image.page_number, image.xref, image.placement_index)
                if existing:
                    image.image_id = existing["id"]
                    continue
            # Use deterministic image_id so Blob storage keys are idempotent across retries and DB failures.
            image.image_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"alttext-image:{project_id}:{image.page_number}:{image.xref}:{image.placement_index}",
                )
            )
            images_to_upload.append(image)
    dur_db_lookup = time.perf_counter() - t0_lookup

    if not images_to_upload:
        if metrics is not None:
            metrics["dur_db_lookup"] += dur_db_lookup
            metrics["db_lookup_count"] += db_lookup_count
            metrics["batch_last"] = {
                "dur_db_lookup": dur_db_lookup,
                "dur_blob_upload": 0.0,
                "dur_db_insert": 0.0,
                "dur_blob_verify": 0.0,
                "db_lookup_count": db_lookup_count,
                "blob_upload_count": 0,
                "db_insert_count": 0,
                "blob_verify_count": 0,
            }
        return context

    def _save_one(img):
        payload = img.image_bytes
        if not payload and batch_temp_dir and img.suggested_filename:
            disk_path = os.path.join(batch_temp_dir, img.suggested_filename)
            if os.path.isfile(disk_path):
                with open(disk_path, "rb") as f:
                    payload = f.read()
        key, checksum, size = filestore.save_image(
            project_id=project_id,
            image_id=img.image_id,
            image_bytes=payload,
            ext=img.image_format,
        )
        return img, key, checksum, size

    t0_upload = time.perf_counter()
    uploaded = []
    first_upload_error = None
    max_upload_workers = min(4, len(images_to_upload))
    if max_upload_workers > 1:
        with ThreadPoolExecutor(max_workers=max_upload_workers) as uploader:
            future_map = {uploader.submit(_save_one, img): img for img in images_to_upload}
            for future in as_completed(future_map):
                try:
                    uploaded.append(future.result())
                except Exception as exc:
                    if first_upload_error is None:
                        first_upload_error = exc
    else:
        for img in images_to_upload:
            try:
                uploaded.append(_save_one(img))
            except Exception as exc:
                if first_upload_error is None:
                    first_upload_error = exc
    dur_blob_upload = time.perf_counter() - t0_upload
    blob_upload_count = len(uploaded)

    # Persist all successfully uploaded images before propagating any partial Blob upload error
    # so retries do not re-upload or orphan already-persisted assets.
    t0_insert = time.perf_counter()
    db_insert_count = 0
    if uploaded:
        if hasattr(repo, "create_image_assets_batch"):
            batch_items = [
                {
                    "image_id": img.image_id,
                    "project_id": project_id,
                    "page_number": img.page_number,
                    "xref": img.xref,
                    "placement_index": img.placement_index,
                    "bbox": img.bbox.to_dict(),
                    "original_caption": img.caption,
                    "caption_position": img.caption_position,
                    "needs_caption_review": img.needs_caption_review,
                    "review_reasons": img.review_reasons,
                    "suggested_filename": img.suggested_filename,
                    "file_storage_key": image_key,
                    "file_checksum": image_checksum,
                    "image_format": img.image_format,
                    "processing_status": "ready_for_review",
                    "group_id": None,
                    "association_type": img.association_type,
                    "association_confidence": img.association_confidence,
                    "panel_label": img.panel_label,
                }
                for img, image_key, image_checksum, _ in uploaded
            ]
            repo.create_image_assets_batch(batch_items)
            db_insert_count = 1
        else:
            for img, image_key, image_checksum, _ in uploaded:
                repo.create_image_asset(
                    image_id=img.image_id, project_id=project_id, page_number=img.page_number,
                    xref=img.xref, placement_index=img.placement_index, bbox=img.bbox.to_dict(),
                    original_caption=img.caption, caption_position=img.caption_position,
                    needs_caption_review=img.needs_caption_review, review_reasons=img.review_reasons,
                    suggested_filename=img.suggested_filename, file_storage_key=image_key,
                    file_checksum=image_checksum, image_format=img.image_format,
                    processing_status="ready_for_review", group_id=None,
                    association_type=img.association_type,
                    association_confidence=img.association_confidence, panel_label=img.panel_label,
                )
                db_insert_count += 1
        for img, _, _, _ in uploaded:
            img.image_bytes = b""
            if known_source_map is not None:
                known_source_map[(int(img.page_number), int(img.xref), int(img.placement_index))] = img.image_id
    dur_db_insert = time.perf_counter() - t0_insert

    if metrics is not None:
        metrics["dur_db_lookup"] += dur_db_lookup
        metrics["db_lookup_count"] += db_lookup_count
        metrics["dur_blob_upload"] += dur_blob_upload
        metrics["blob_upload_count"] += blob_upload_count
        metrics["dur_db_insert"] += dur_db_insert
        metrics["db_insert_count"] += db_insert_count
        metrics["batch_last"] = {
            "dur_db_lookup": dur_db_lookup,
            "dur_blob_upload": dur_blob_upload,
            "dur_db_insert": dur_db_insert,
            "dur_blob_verify": 0.0,
            "db_lookup_count": db_lookup_count,
            "blob_upload_count": blob_upload_count,
            "db_insert_count": db_insert_count,
            "blob_verify_count": 0,
        }

    if first_upload_error is not None:
        raise first_upload_error

    return context


def _finalize_document_grouping(repo, project_id, engine, contexts, metrics=None):
    images_by_page, text_blocks_by_page, page_dimensions = {}, {}, {}
    for context in contexts:
        images_by_page.update(context.get("images_by_page", {}))
        text_blocks_by_page.update(context.get("text_blocks_by_page", {}))
        page_dimensions.update(context.get("page_dimensions", {}))
    final_images, groups = engine.group_extracted_pages(images_by_page, text_blocks_by_page, page_dimensions)

    group_ids = {}
    group_payloads = []
    for group in groups:
        stable_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
            f"{project_id}:{group.caption_page_number}:{group.caption_text}:{group.figure_label or ''}"))
        group_ids[group.group_id] = stable_id
        group_payloads.append({
            "group_id": stable_id,
            "project_id": project_id,
            "caption_text": group.caption_text,
            "caption_page_number": group.caption_page_number,
            "caption_position": group.caption_position or "below",
            "figure_label": group.figure_label,
            "is_cross_page": group.is_cross_page,
            "review_status": group.review_status,
            "review_reasons": group.review_reasons,
        })

    groups_created = 0
    if group_payloads:
        if hasattr(repo, "create_figure_groups_batch"):
            groups_created = repo.create_figure_groups_batch(group_payloads)
        else:
            for g in group_payloads:
                if not repo.get_figure_group(g["group_id"]):
                    repo.create_figure_group(
                        group_id=g["group_id"], project_id=project_id, caption_text=g["caption_text"],
                        caption_page_number=g["caption_page_number"], caption_position=g["caption_position"],
                        figure_label=g["figure_label"], is_cross_page=g["is_cross_page"],
                        review_status=g["review_status"], review_reasons=g["review_reasons"],
                    )
                    groups_created += 1

    fallback_source_map = None
    association_updates = []
    for image in final_images:
        existing_id = getattr(image, "image_id", None)
        if not existing_id:
            if fallback_source_map is None and hasattr(repo, "list_image_asset_source_keys"):
                fallback_source_map = repo.list_image_asset_source_keys(project_id)
            if fallback_source_map is not None:
                existing_id = fallback_source_map.get((int(image.page_number), int(image.xref), int(image.placement_index)))
            else:
                existing = repo.find_image_asset_by_source(project_id, image.page_number, image.xref, image.placement_index)
                existing_id = existing["id"] if existing else None
        if not existing_id:
            continue
        image.group_id = group_ids.get(image.group_id, image.group_id)
        association_updates.append({
            "image_id": existing_id,
            "group_id": image.group_id,
            "association_type": image.association_type,
            "association_confidence": image.association_confidence,
            "panel_label": image.panel_label,
            "caption": image.caption,
            "caption_position": image.caption_position,
            "needs_caption_review": image.needs_caption_review,
            "review_reasons": image.review_reasons,
        })

    if association_updates:
        if hasattr(repo, "update_image_group_associations_batch"):
            repo.update_image_group_associations_batch(association_updates)
        else:
            for u in association_updates:
                repo.update_image_group_association(
                    u["image_id"], u["group_id"], association_type=u["association_type"],
                    association_confidence=u["association_confidence"], panel_label=u["panel_label"],
                    caption=u["caption"], caption_position=u["caption_position"],
                    needs_caption_review=u["needs_caption_review"], review_reasons=u["review_reasons"],
                )

    if metrics is not None:
        metrics["groups_created"] = groups_created
        metrics["group_assoc_updates"] = len(association_updates)
    return len(final_images), len(groups)


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
            if stopped.is_set():
                return
            try:
                renewed = repo.renew_job_lease(job_id, worker_id, _iso(datetime.now(timezone.utc)))
                if not renewed:
                    if not stopped.is_set():
                        lease_lost.set()
                        logger.warning("Worker %s lost lease for extraction job %s", worker_id, job_id)
                    return
            except Exception:
                if not stopped.is_set():
                    logger.exception("Could not renew extraction job lease %s", job_id)

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()

    def _stop_heartbeat(join_timeout: float = 5.0) -> None:
        stopped.set()
        if thread.is_alive():
            thread.join(timeout=join_timeout)
    tmp_path = None
    t0_job = time.perf_counter()
    try:
        repo.update_project_status(project_id, status="extracting")
        t0_download = time.perf_counter()
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name

        if hasattr(filestore, "download_to_file"):
            _, source_checksum = filestore.download_to_file(
                project_id, project["source_storage_key"], tmp_path
            )
            with open(tmp_path, "rb") as f:
                header = f.read(5)
            if header != b"%PDF-":
                raise ValueError("Stored source is not a PDF")
            repo.update_project_source_checksum(project_id, source_checksum)
        else:
            source = filestore.read_bytes(project_id, project["source_storage_key"])
            if not source.startswith(b"%PDF-"):
                raise ValueError("Stored source is not a PDF")
            repo.update_project_source_checksum(project_id, hashlib.sha256(source).hexdigest())
            with open(tmp_path, "wb") as f:
                f.write(source)
            del source
        dur_download = time.perf_counter() - t0_download

        t0_open = time.perf_counter()
        doc = fitz.open(tmp_path)
        total_document_pages = len(doc)
        doc.close()
        dur_open = time.perf_counter() - t0_open

        page_numbers = claimed.get("page_numbers")
        if not page_numbers and claimed.get("page_start") and claimed.get("page_end"):
            page_numbers = list(range(int(claimed["page_start"]), int(claimed["page_end"]) + 1))
        if not page_numbers:
            page_numbers = list(range(1, total_document_pages + 1))
        page_numbers = sorted({int(page) for page in page_numbers if 1 <= int(page) <= total_document_pages})
        if not page_numbers:
            raise ValueError("Extraction job has no valid pages to process")

        batch_size = _env_int("PDF_BATCH_SIZE", 25)
        max_parallel = _env_int("MAX_PARALLEL_BATCHES", 1, minimum=1, maximum=4)
        page_batches = [page_numbers[i:i + batch_size] for i in range(0, len(page_numbers), batch_size)]
        t0_init = time.perf_counter()
        repo.initialize_job_batches(job_id, page_batches, total_document_pages, max_attempts=max_retries)
        batch_rows = {row["batch_index"]: row for row in repo.list_job_batches(job_id)}
        dur_init_batches = time.perf_counter() - t0_init

        contexts = []
        active_engine = engine or ExtractionEngine()
        use_processes = engine is None and max_parallel > 1 and len(page_batches) > 1
        job_metrics = {
            "dur_init_batches": dur_init_batches,
            "dur_start_batch": 0.0,
            "start_batch_count": 0,
            "dur_renew_lease": 0.0,
            "renew_lease_count": 0,
            "dur_complete_batch": 0.0,
            "complete_batch_count": 0,
            "dur_db_lookup": 0.0,
            "db_lookup_count": 0,
            "dur_blob_upload": 0.0,
            "blob_upload_count": 0,
            "dur_db_insert": 0.0,
            "db_insert_count": 0,
            "dur_blob_verify": 0.0,
            "blob_verify_count": 0,
            "groups_created": 0,
            "group_assoc_updates": 0,
        }
        if hasattr(repo, "list_image_asset_source_keys"):
            t0_job_lookup = time.perf_counter()
            known_source_map = repo.list_image_asset_source_keys(project_id)
            job_metrics["dur_db_lookup"] += time.perf_counter() - t0_job_lookup
            job_metrics["db_lookup_count"] += 1
        else:
            known_source_map = None

        last_lease_check = [time.monotonic()]

        def _ensure_lease():
            if stopped.is_set():
                return
            if lease_lost.is_set():
                raise RuntimeError("Worker lost the extraction job lease")
            now_mono = time.monotonic()
            if now_mono - last_lease_check[0] >= heartbeat_seconds:
                t0_lease = time.perf_counter()
                ok = repo.renew_job_lease(job_id, worker_id, _iso(datetime.now(timezone.utc)))
                job_metrics["dur_renew_lease"] += time.perf_counter() - t0_lease
                job_metrics["renew_lease_count"] += 1
                if not ok:
                    lease_lost.set()
                    raise RuntimeError("Worker lost the extraction job lease")
                last_lease_check[0] = now_mono

        logger.info(
            "Job started: job_id=%s project_id=%s worker_id=%s total_pages=%d total_batches=%d batch_size=%d max_parallel=%d duration_download=%.3fs duration_open=%.3fs dur_init_batches=%.3fs",
            job_id, project_id, worker_id, total_document_pages, len(page_batches), batch_size, max_parallel, dur_download, dur_open, dur_init_batches,
        )

        if use_processes:
            _process_batches_parallel(
                job_id, project_id, tmp_path, repo, filestore, page_batches,
                batch_rows, contexts, active_engine, max_parallel, worker_id, lease_lost,
                metrics=job_metrics, known_source_map=known_source_map, ensure_lease=_ensure_lease,
            )
        else:
            for index, pages in enumerate(page_batches):
                t0_batch = time.perf_counter()
                _ensure_lease()
                batch = batch_rows[index]
                if batch["status"] != "completed":
                    t0_sb = time.perf_counter()
                    started_ok = repo.start_job_batch(
                        job_id, index, _iso(datetime.now(timezone.utc)), worker_id=worker_id
                    )
                    job_metrics["dur_start_batch"] += time.perf_counter() - t0_sb
                    job_metrics["start_batch_count"] += 1
                    if not started_ok:
                        raise RuntimeError(f"Extraction batch {index + 1} exhausted its retry limit")
                batch_temp_dir = None
                try:
                    t0_extract = time.perf_counter()
                    result, batch_temp_dir = _process_batch(tmp_path, pages, engine)
                    dur_extract = time.perf_counter() - t0_extract
                    _ensure_lease()
                    t0_persist = time.perf_counter()
                    contexts.append(_persist_batch_images(
                        repo, filestore, project_id, result,
                        batch_temp_dir=batch_temp_dir, metrics=job_metrics,
                        known_source_map=known_source_map,
                    ))
                    dur_persist = time.perf_counter() - t0_persist
                finally:
                    if batch_temp_dir and os.path.exists(batch_temp_dir):
                        shutil.rmtree(batch_temp_dir, ignore_errors=True)
                    gc.collect()
                if batch["status"] != "completed":
                    if result.errors:
                        repo.set_job_batch_status(
                            job_id, index, "failed", repr(result.errors[:3]), worker_id=worker_id
                        )
                        raise RuntimeError(f"Page batch {index + 1} reported extraction errors")
                    t0_cb = time.perf_counter()
                    completed_ok = repo.set_job_batch_status(job_id, index, "completed", worker_id=worker_id)
                    job_metrics["dur_complete_batch"] += time.perf_counter() - t0_cb
                    job_metrics["complete_batch_count"] += 1
                    if not completed_ok:
                        raise RuntimeError("Worker lost ownership before recording batch completion")
                dur_batch = time.perf_counter() - t0_batch
                b_last = job_metrics.get("batch_last") or {}
                logger.info(
                    "Batch processed: job_id=%s batch_index=%d/%d page_range=%s pages_count=%d images_discovered=%d duration_extract=%.3fs dur_db_lookup=%.3fs dur_blob_upload=%.3fs dur_db_insert=%.3fs dur_blob_verify=%.3fs duration_persist=%.3fs duration_total=%.3fs status=completed",
                    job_id, index + 1, len(page_batches), f"{pages[0]}-{pages[-1]}", len(pages), len(result.images), dur_extract,
                    b_last.get("dur_db_lookup", 0.0), b_last.get("dur_blob_upload", 0.0), b_last.get("dur_db_insert", 0.0), b_last.get("dur_blob_verify", 0.0),
                    dur_persist, dur_batch,
                )

        _ensure_lease()
        t0_group = time.perf_counter()
        img_count, group_count = _finalize_document_grouping(repo, project_id, active_engine, contexts, metrics=job_metrics)
        dur_group = time.perf_counter() - t0_group
        _stop_heartbeat()
        if lease_lost.is_set():
            raise RuntimeError("Worker lost the extraction job lease")
        if not repo.update_claimed_job_status(job_id, worker_id, "completed"):
            raise RuntimeError("Worker lost ownership before completing the extraction job")
        repo.update_project_status(project_id, status="ready_for_review")
        dur_job = time.perf_counter() - t0_job
        logger.info(
            "Job completed: job_id=%s project_id=%s total_pages=%d total_batches=%d total_images=%d total_groups=%d "
            "dur_download=%.3fs dur_init_batches=%.3fs dur_start_batch=%.3fs dur_renew_lease=%.3fs dur_complete_batch=%.3fs "
            "dur_db_lookup=%.3fs dur_blob_upload=%.3fs dur_db_insert=%.3fs dur_blob_verify=%.3fs dur_grouping=%.3fs dur_job=%.3fs "
            "start_batches=%d renew_leases=%d complete_batches=%d db_lookups=%d blob_uploads=%d db_inserts=%d blob_verifies=%d groups_created=%d group_assoc_updates=%d status=completed",
            job_id, project_id, total_document_pages, len(page_batches), img_count, group_count,
            dur_download, job_metrics["dur_init_batches"], job_metrics["dur_start_batch"], job_metrics["dur_renew_lease"], job_metrics["dur_complete_batch"],
            job_metrics["dur_db_lookup"], job_metrics["dur_blob_upload"], job_metrics["dur_db_insert"], job_metrics["dur_blob_verify"],
            dur_group, dur_job,
            job_metrics["start_batch_count"], job_metrics["renew_lease_count"], job_metrics["complete_batch_count"],
            job_metrics["db_lookup_count"], job_metrics["blob_upload_count"], job_metrics["db_insert_count"], job_metrics["blob_verify_count"],
            job_metrics["groups_created"], job_metrics["group_assoc_updates"],
        )
        return True
    except Exception as exc:
        _stop_heartbeat()
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
        _stop_heartbeat()
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                logger.warning("Could not remove temporary worker PDF %s", tmp_path)


def _process_batches_parallel(job_id, project_id, pdf_path, repo, filestore, page_batches,
                              batch_rows, contexts, engine, max_parallel, worker_id, lease_lost,
                              metrics=None, known_source_map=None, ensure_lease=None):
    pending = iter(enumerate(page_batches))
    active = {}
    batch_start_times = {}
    first_error = None
    with ProcessPoolExecutor(max_workers=max_parallel) as executor:
        while True:
            while first_error is None and len(active) < max_parallel:
                try:
                    if ensure_lease is not None:
                        ensure_lease()
                    elif lease_lost.is_set() or not repo.renew_job_lease(
                        job_id, worker_id, _iso(datetime.now(timezone.utc))
                    ):
                        lease_lost.set()
                        raise RuntimeError("Worker lost the extraction job lease")
                except Exception as exc:
                    first_error = exc
                    break
                try:
                    index, pages = next(pending)
                except StopIteration:
                    break
                batch = batch_rows[index]
                if batch["status"] != "completed":
                    t0_sb = time.perf_counter()
                    started_ok = repo.start_job_batch(
                        job_id, index, _iso(datetime.now(timezone.utc)), worker_id=worker_id
                    )
                    if metrics is not None:
                        metrics["dur_start_batch"] = metrics.get("dur_start_batch", 0.0) + (time.perf_counter() - t0_sb)
                        metrics["start_batch_count"] = metrics.get("start_batch_count", 0) + 1
                    if not started_ok:
                        first_error = RuntimeError(f"Extraction batch {index + 1} exhausted its retry limit")
                        break
                batch_start_times[index] = time.perf_counter()
                active[executor.submit(_extract_pdf_batch, pdf_path, pages)] = (index, batch)
            if not active:
                break
            done, _ = wait(active, return_when=FIRST_COMPLETED)
            for future in done:
                index, batch = active.pop(future)
                try:
                    result, batch_temp_dir = future.result()
                    try:
                        if ensure_lease is not None:
                            ensure_lease()
                        elif lease_lost.is_set() or not repo.renew_job_lease(
                            job_id, worker_id, _iso(datetime.now(timezone.utc))
                        ):
                            lease_lost.set()
                            raise RuntimeError("Worker lost the extraction job lease")
                    except Exception:
                        if batch_temp_dir and os.path.exists(batch_temp_dir):
                            shutil.rmtree(batch_temp_dir, ignore_errors=True)
                        raise
                    t0_persist = time.perf_counter()
                    contexts.append(_persist_batch_images(
                        repo, filestore, project_id, result,
                        batch_temp_dir=batch_temp_dir, metrics=metrics,
                        known_source_map=known_source_map,
                    ))
                    dur_persist = time.perf_counter() - t0_persist
                    if batch_temp_dir and os.path.exists(batch_temp_dir):
                        shutil.rmtree(batch_temp_dir, ignore_errors=True)
                    if result.errors:
                        raise RuntimeError(f"Page batch {index + 1} reported extraction errors: {result.errors[:3]}")
                    if batch["status"] != "completed":
                        t0_cb = time.perf_counter()
                        completed_ok = repo.set_job_batch_status(job_id, index, "completed", worker_id=worker_id)
                        if metrics is not None:
                            metrics["dur_complete_batch"] = metrics.get("dur_complete_batch", 0.0) + (time.perf_counter() - t0_cb)
                            metrics["complete_batch_count"] = metrics.get("complete_batch_count", 0) + 1
                        if not completed_ok:
                            raise RuntimeError("Worker lost ownership before recording batch completion")
                    dur_batch = time.perf_counter() - batch_start_times.get(index, t0_persist)
                    pages = page_batches[index]
                    b_last = (metrics or {}).get("batch_last") or {}
                    logger.info(
                        "Batch processed: job_id=%s batch_index=%d/%d page_range=%s pages_count=%d images_discovered=%d dur_db_lookup=%.3fs dur_blob_upload=%.3fs dur_db_insert=%.3fs dur_blob_verify=%.3fs duration_persist=%.3fs duration_total=%.3fs status=completed",
                        job_id, index + 1, len(page_batches), f"{pages[0]}-{pages[-1]}", len(pages), len(result.images),
                        b_last.get("dur_db_lookup", 0.0), b_last.get("dur_blob_upload", 0.0), b_last.get("dur_db_insert", 0.0), b_last.get("dur_blob_verify", 0.0),
                        dur_persist, dur_batch,
                    )
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

