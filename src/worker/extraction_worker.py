"""Crash-recoverable extraction job execution shared by Render and tests."""

import atexit
import gc
import hashlib
import logging
import os
import queue
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
DEFAULT_PERSIST_MICRO_BATCH_SIZE = 8
DEFAULT_PERSIST_FLUSH_SECONDS = 1.5
PERSIST_QUEUE_MAXSIZE = 16

_WORKER_DOC = None
_WORKER_TEMP_DIR = None
_WORKER_ENGINE = None


def _close_page_worker():
    global _WORKER_DOC
    if _WORKER_DOC is not None:
        try:
            _WORKER_DOC.close()
        except Exception:
            pass
        _WORKER_DOC = None


def _init_page_worker(pdf_path, temp_dir):
    global _WORKER_DOC, _WORKER_TEMP_DIR, _WORKER_ENGINE
    _close_page_worker()
    _WORKER_DOC = fitz.open(pdf_path)
    _WORKER_TEMP_DIR = temp_dir
    _WORKER_ENGINE = ExtractionEngine()
    atexit.register(_close_page_worker)


def _extract_single_page(page_num):
    """Extract a single 1-based page using the worker's persistent read-only fitz.Document."""
    if _WORKER_DOC is None or _WORKER_ENGINE is None:
        raise RuntimeError("Page worker is not initialized")
    page_images, text_blocks, page_dim, page_errors, _ = _WORKER_ENGINE.extract_page(
        _WORKER_DOC,
        page_num,
        output_images_dir=_WORKER_TEMP_DIR,
        disk_filename_by_id=True,
    )
    disk_paths = {}
    for img in page_images:
        if _WORKER_TEMP_DIR:
            candidate = os.path.join(_WORKER_TEMP_DIR, f"{img.image_id}.{img.image_format}")
            if os.path.isfile(candidate):
                disk_paths[img.image_id] = candidate
                img.image_bytes = b""
    return page_num, page_images, text_blocks, page_dim, page_errors, disk_paths


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


_ORIG_EXTRACT_PDF_BATCH = _extract_pdf_batch


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


_ORIG_EXTRACT_PDF_BATCH = _extract_pdf_batch


def _persist_images_list(repo, filestore, project_id, image_entries, metrics=None, known_source_map=None):
    """
    Persist a list of (ExtractedImage, optional_disk_path) items using
    job-scoped source-map caching, deterministic UUID5 IDs, bounded Blob upload
    concurrency, and batch DB insertion.
    """
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
    skipped_disk_paths = []
    for image, disk_path in image_entries:
        src_key = (int(image.page_number), int(image.xref), int(image.placement_index))
        if src_key in seen_keys:
            if disk_path:
                skipped_disk_paths.append(disk_path)
            continue
        seen_keys.add(src_key)
        if existing_map is not None:
            existing_id = existing_map.get(src_key)
            if existing_id:
                image.image_id = existing_id
                if disk_path:
                    skipped_disk_paths.append(disk_path)
                continue
        else:
            db_lookup_count += 1
            existing = repo.find_image_asset_by_source(project_id, image.page_number, image.xref, image.placement_index)
            if existing:
                image.image_id = existing["id"]
                if disk_path:
                    skipped_disk_paths.append(disk_path)
                continue
        # Use deterministic image_id so Blob storage keys are idempotent across retries and DB failures.
        image.image_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"alttext-image:{project_id}:{image.page_number}:{image.xref}:{image.placement_index}",
            )
        )
        images_to_upload.append((image, disk_path))
    dur_db_lookup = time.perf_counter() - t0_lookup

    for p in skipped_disk_paths:
        try:
            if os.path.isfile(p):
                os.unlink(p)
        except OSError:
            pass

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
        return 0

    def _save_one(entry):
        img, disk_path = entry
        payload = img.image_bytes
        if not payload and disk_path and os.path.isfile(disk_path):
            with open(disk_path, "rb") as f:
                payload = f.read()
        key, checksum, size = filestore.save_image(
            project_id=project_id,
            image_id=img.image_id,
            image_bytes=payload,
            ext=img.image_format,
        )
        if disk_path and os.path.isfile(disk_path):
            try:
                os.unlink(disk_path)
            except OSError:
                pass
        return img, key, checksum, size

    t0_upload = time.perf_counter()
    uploaded = []
    first_upload_error = None
    max_upload_workers = min(4, len(images_to_upload))
    if max_upload_workers > 1:
        with ThreadPoolExecutor(max_workers=max_upload_workers) as uploader:
            future_map = {uploader.submit(_save_one, entry): entry for entry in images_to_upload}
            for future in as_completed(future_map):
                try:
                    uploaded.append(future.result())
                except Exception as exc:
                    if first_upload_error is None:
                        first_upload_error = exc
    else:
        for entry in images_to_upload:
            try:
                uploaded.append(_save_one(entry))
            except Exception as exc:
                if first_upload_error is None:
                    first_upload_error = exc
    dur_blob_upload = time.perf_counter() - t0_upload
    blob_upload_count = len(uploaded)

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

    return len(uploaded)


def _persist_batch_images(repo, filestore, project_id, result, batch_temp_dir=None, metrics=None, known_source_map=None):
    context = result.grouping_context or {}
    images_by_page = context.get("images_by_page", {})
    entries = []
    for page in sorted(images_by_page):
        for image in images_by_page[page]:
            disk_path = None
            if batch_temp_dir and image.suggested_filename:
                candidate = os.path.join(batch_temp_dir, image.suggested_filename)
                if os.path.isfile(candidate):
                    disk_path = candidate
            entries.append((image, disk_path))

    _persist_images_list(
        repo,
        filestore,
        project_id,
        entries,
        metrics=metrics,
        known_source_map=known_source_map,
    )
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

    existing_imgs_by_id = (
        {row["id"]: row for row in repo.list_project_images(project_id)}
        if hasattr(repo, "list_project_images")
        else {}
    )
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
        existing_row = existing_imgs_by_id.get(existing_id) or {}
        editor_modified = bool(
            existing_row
            and existing_row.get("updated_at")
            and existing_row.get("created_at")
            and existing_row.get("updated_at") != existing_row.get("created_at")
        )
        if existing_row.get("group_id"):
            # Preserve manual grouping created by the editor during live extraction
            image.group_id = existing_row["group_id"]
            assoc_type = existing_row.get("association_type") or image.association_type
        else:
            image.group_id = group_ids.get(image.group_id, image.group_id)
            assoc_type = image.association_type
        caption_to_save = existing_row.get("original_caption", image.caption) if editor_modified else image.caption
        association_updates.append({
            "image_id": existing_id,
            "group_id": image.group_id,
            "association_type": assoc_type,
            "association_confidence": image.association_confidence,
            "panel_label": image.panel_label,
            "caption": caption_to_save,
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
        use_continuous = (
            engine is None
            and max_parallel > 1
            and len(page_numbers) > 1
            and _extract_pdf_batch is _ORIG_EXTRACT_PDF_BATCH
        )
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

        if use_continuous:
            _process_pages_continuous(
                job_id, project_id, tmp_path, repo, filestore, page_batches,
                batch_rows, contexts, max_parallel, worker_id, lease_lost,
                metrics=job_metrics, known_source_map=known_source_map, ensure_lease=_ensure_lease,
            )
        elif use_processes:
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


def _process_pages_continuous(
    job_id, project_id, pdf_path, repo, filestore, page_batches,
    batch_rows, contexts, max_parallel, worker_id, lease_lost,
    metrics=None, known_source_map=None, ensure_lease=None,
):
    """
    Option C: Continuous page extraction with bounded IPC & persistence micro-batching.
    - Each worker process opens pdf_path read-only once in _init_page_worker.
    - Page 1 is dispatched first; the very first extracted image flushes immediately.
    - Subsequent images flush in bounded micro-batches (PERSIST_MICRO_BATCH_SIZE / PERSIST_FLUSH_INTERVAL_SECONDS).
    - Coarse job_batches checkpoints are marked completed only after all pages in that batch are persisted.
    """
    micro_batch_size = _env_int("PERSIST_MICRO_BATCH_SIZE", DEFAULT_PERSIST_MICRO_BATCH_SIZE, minimum=1, maximum=32)
    try:
        flush_interval = float(os.environ.get("PERSIST_FLUSH_INTERVAL_SECONDS", str(DEFAULT_PERSIST_FLUSH_SECONDS)))
    except (TypeError, ValueError):
        flush_interval = DEFAULT_PERSIST_FLUSH_SECONDS
    flush_interval = max(0.05, min(10.0, flush_interval))

    job_temp_dir = tempfile.mkdtemp(prefix="pdf_cont_")
    persist_queue = queue.Queue(maxsize=PERSIST_QUEUE_MAXSIZE)
    persist_error = [None]
    persist_stop = threading.Event()
    batch_lock = threading.Lock()

    # Track per-batch extraction & persistence completion so coarse checkpoints stay accurate
    batch_remaining_extract = {}
    batch_remaining_persist = {}
    batch_started = {}
    page_tasks = []
    for batch_idx, pages in enumerate(page_batches):
        batch = batch_rows[batch_idx]
        if batch["status"] == "completed":
            continue
        batch_remaining_extract[batch_idx] = len(pages)
        batch_remaining_persist[batch_idx] = len(pages)
        batch_started[batch_idx] = False
        for pno in pages:
            page_tasks.append((batch_idx, pno))

    completed_batches_ready = queue.Queue()

    def _persistence_loop():
        first_image_flushed = False
        buffer = []
        buffer_batch_counts = {}
        buffer_started_at = None

        def _mark_batches_persisted(counts_map):
            for b_idx, cnt in counts_map.items():
                if cnt <= 0:
                    continue
                with batch_lock:
                    batch_remaining_persist[b_idx] -= cnt
                    if batch_remaining_persist[b_idx] == 0 and batch_remaining_extract[b_idx] == 0:
                        completed_batches_ready.put(b_idx)

        def _flush():
            nonlocal buffer, buffer_batch_counts, buffer_started_at
            if not buffer and not buffer_batch_counts:
                return
            if buffer:
                _persist_images_list(
                    repo,
                    filestore,
                    project_id,
                    buffer,
                    metrics=metrics,
                    known_source_map=known_source_map,
                )
            _mark_batches_persisted(buffer_batch_counts)
            buffer = []
            buffer_batch_counts = {}
            buffer_started_at = None

        try:
            sentinel_seen = False
            while not sentinel_seen:
                timeout = 0.05 if buffer else 0.25
                try:
                    item = persist_queue.get(timeout=timeout)
                except queue.Empty:
                    if buffer and buffer_started_at is not None and (time.monotonic() - buffer_started_at) >= flush_interval:
                        _flush()
                    continue

                if item is None:
                    sentinel_seen = True
                    _flush()
                    break

                b_idx, entries = item
                if entries and not first_image_flushed:
                    # Immediate flush for the very first extracted image (TTFI priority)
                    first_entry = entries[0]
                    _persist_images_list(
                        repo,
                        filestore,
                        project_id,
                        [first_entry],
                        metrics=metrics,
                        known_source_map=known_source_map,
                    )
                    first_image_flushed = True
                    entries = entries[1:]
                    if not entries:
                        _mark_batches_persisted({b_idx: 1})
                        continue

                if entries:
                    if not buffer:
                        buffer_started_at = time.monotonic()
                    buffer.extend(entries)
                buffer_batch_counts[b_idx] = buffer_batch_counts.get(b_idx, 0) + 1

                if len(buffer) >= micro_batch_size or (
                    buffer and buffer_started_at is not None and (time.monotonic() - buffer_started_at) >= flush_interval
                ):
                    _flush()
                elif not buffer and buffer_batch_counts:
                    _mark_batches_persisted(buffer_batch_counts)
                    buffer_batch_counts = {}
        except Exception as exc:
            persist_error[0] = exc
            persist_stop.set()

    persist_thread = threading.Thread(target=_persistence_loop, daemon=True)
    persist_thread.start()

    job_context = {
        "images_by_page": {},
        "text_blocks_by_page": {},
        "page_dimensions": {},
    }
    contexts.append(job_context)

    def _drain_completed_batches():
        while True:
            try:
                b_idx = completed_batches_ready.get_nowait()
            except queue.Empty:
                break
            t0_cb = time.perf_counter()
            ok = repo.set_job_batch_status(job_id, b_idx, "completed", worker_id=worker_id)
            if metrics is not None:
                metrics["dur_complete_batch"] = metrics.get("dur_complete_batch", 0.0) + (time.perf_counter() - t0_cb)
                metrics["complete_batch_count"] = metrics.get("complete_batch_count", 0) + 1
            if not ok:
                raise RuntimeError("Worker lost ownership before recording batch completion")

    max_in_flight = max(2, max_parallel * 2)
    pending = iter(page_tasks)
    active = {}
    first_error = None

    try:
        with ProcessPoolExecutor(
            max_workers=max_parallel,
            initializer=_init_page_worker,
            initargs=(pdf_path, job_temp_dir),
        ) as executor:
            while True:
                if persist_error[0] is not None and first_error is None:
                    first_error = persist_error[0]

                while first_error is None and len(active) < max_in_flight:
                    try:
                        if ensure_lease is not None:
                            ensure_lease()
                        elif lease_lost.is_set():
                            raise RuntimeError("Worker lost the extraction job lease")
                    except Exception as exc:
                        first_error = exc
                        break
                    try:
                        b_idx, pno = next(pending)
                    except StopIteration:
                        break

                    if not batch_started[b_idx]:
                        t0_sb = time.perf_counter()
                        started_ok = repo.start_job_batch(
                            job_id, b_idx, _iso(datetime.now(timezone.utc)), worker_id=worker_id
                        )
                        if metrics is not None:
                            metrics["dur_start_batch"] = metrics.get("dur_start_batch", 0.0) + (time.perf_counter() - t0_sb)
                            metrics["start_batch_count"] = metrics.get("start_batch_count", 0) + 1
                        if not started_ok:
                            first_error = RuntimeError(f"Extraction batch {b_idx + 1} exhausted its retry limit")
                            break
                        batch_started[b_idx] = True

                    active[executor.submit(_extract_single_page, pno)] = (b_idx, pno)

                if not active:
                    break

                done, _ = wait(active, return_when=FIRST_COMPLETED)
                for future in done:
                    b_idx, pno = active.pop(future)
                    if first_error is not None:
                        continue
                    try:
                        page_num, page_images, text_blocks, page_dim, page_errors, disk_paths = future.result()
                        if page_errors:
                            raise RuntimeError(f"Page {page_num} reported extraction errors: {page_errors[:3]}")
                        if page_dim is not None:
                            job_context["images_by_page"][page_num] = page_images
                            job_context["text_blocks_by_page"][page_num] = text_blocks
                            job_context["page_dimensions"][page_num] = page_dim

                        with batch_lock:
                            batch_remaining_extract[b_idx] -= 1

                        entries = [(img, disk_paths.get(img.image_id)) for img in page_images]
                        while not persist_stop.is_set():
                            try:
                                persist_queue.put((b_idx, entries), timeout=0.25)
                                break
                            except queue.Full:
                                if persist_error[0] is not None:
                                    raise persist_error[0]
                        if persist_error[0] is not None:
                            raise persist_error[0]
                    except Exception as exc:
                        if first_error is None:
                            first_error = exc

                _drain_completed_batches()

                if first_error is not None:
                    for fut in list(active):
                        fut.cancel()
                    break

        # Drain persistence queue cleanly before grouping or raising
        if not persist_stop.is_set():
            while True:
                try:
                    persist_queue.put(None, timeout=0.25)
                    break
                except queue.Full:
                    if persist_error[0] is not None:
                        break
        persist_thread.join(timeout=30)

        if first_error is None and persist_error[0] is not None:
            first_error = persist_error[0]

        if first_error is not None:
            raise first_error

        _drain_completed_batches()
    finally:
        persist_stop.set()
        if persist_thread.is_alive():
            try:
                persist_queue.put_nowait(None)
            except queue.Full:
                pass
            persist_thread.join(timeout=5)
        if os.path.exists(job_temp_dir):
            shutil.rmtree(job_temp_dir, ignore_errors=True)
