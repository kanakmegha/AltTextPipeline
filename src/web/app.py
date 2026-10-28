"""
Extraction Inspector - FastAPI Application
------------------------------------------
Local web interface allowing editors and engineers to upload PDFs, run the
Phase 2 modular extraction engine, view extracted assets and captions, and inspect
geometric candidates and review flags.
"""

import os
import uuid
from pathlib import Path
from typing import Optional, List
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from src.db.connection import DatabaseConnection
from src.db.migrations import apply_migrations
from src.db.repository import Repository
from src.storage.filestore import FileStore
from src.extraction.engine import ExtractionEngine


BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DEFAULT_DB_PATH = str(BASE_DIR / "data" / "pipeline.db")
DEFAULT_STORAGE_DIR = str(BASE_DIR / "data" / "storage")
STATIC_DIR = BASE_DIR / "src" / "web" / "static"


def create_app(db_path: str = DEFAULT_DB_PATH, storage_dir: str = DEFAULT_STORAGE_DIR) -> FastAPI:
    app = FastAPI(
        title="AltTextPipeline - Extraction Inspector",
        description="Local web interface to inspect image extraction and caption matching",
        version="0.3.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    db_conn = DatabaseConnection(db_path)
    apply_migrations(db_conn)
    repo = Repository(db_conn)
    filestore = FileStore(storage_dir)
    engine = ExtractionEngine()

    app.state.repo = repo
    app.state.filestore = filestore
    app.state.engine = engine

    # Mount static assets
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index_page():
        index_file = STATIC_DIR / "index.html"
        if not index_file.exists():
            return HTMLResponse("<h2>Extraction Inspector frontend static files missing.</h2>", status_code=404)
        return HTMLResponse(index_file.read_text(encoding="utf-8"))

    @app.get("/api/health")
    async def health_check():
        return {"status": "ok", "app": "Extraction Inspector", "version": "0.3.0"}

    @app.get("/api/sample-pdf-available")
    async def sample_pdf_available():
        sample_path = BASE_DIR / "40345.pdf"
        exists = sample_path.exists()
        return {
            "available": exists,
            "filename": "40345.pdf",
            "size_bytes": sample_path.stat().st_size if exists else 0,
            "suggested_sample_pages": "1, 4, 33, 46, 77"
        }

    @app.get("/api/projects")
    async def list_projects():
        projects = repo.list_projects()
        return {"projects": projects}

    @app.get("/api/projects/{project_id}")
    async def get_project(project_id: str):
        project = repo.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        images = repo.list_project_images(project_id)
        return {
            "project": project,
            "stats": {
                "total_images": len(images),
                "captions_found": sum(1 for img in images if bool(img["original_caption"])),
                "needs_review": sum(1 for img in images if img["needs_caption_review"]),
                "matched": sum(1 for img in images if bool(img["original_caption"]) and not img["needs_caption_review"]),
            }
        }

    @app.get("/api/projects/{project_id}/images")
    async def list_project_images(project_id: str):
        project = repo.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        images = repo.list_project_images(project_id)
        return {"images": images}

    @app.get("/api/projects/{project_id}/images/{image_id}/file")
    async def get_image_file(project_id: str, image_id: str):
        asset = repo.get_image_asset(image_id)
        if not asset or asset["project_id"] != project_id:
            raise HTTPException(status_code=404, detail="Image asset not found")

        try:
            target_path = filestore.resolve_path(project_id, asset["file_storage_key"])
            if not target_path.exists():
                raise HTTPException(status_code=404, detail="Image binary not found on disk")
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

        media_type = f"image/{asset['image_format']}" if asset["image_format"] != "jpg" else "image/jpeg"
        return FileResponse(
            path=str(target_path),
            media_type=media_type,
            filename=asset["suggested_filename"]
        )

    @app.post("/api/upload-and-extract")
    async def upload_and_extract(
        file: Optional[UploadFile] = File(None),
        use_sample_pdf: bool = Form(False),
        pages: Optional[str] = Form(None),
        project_name: Optional[str] = Form(None),
    ):
        """
        Uploads a PDF (or uses the local sample 40345.pdf) and executes extraction.
        """
        project_id = str(uuid.uuid4())

        # Determine source PDF
        if use_sample_pdf:
            sample_path = BASE_DIR / "40345.pdf"
            if not sample_path.exists():
                raise HTTPException(status_code=400, detail="Local sample PDF (40345.pdf) not found.")
            source_bytes = sample_path.read_bytes()
            original_filename = "40345.pdf"
        elif file is not None:
            source_bytes = await file.read()
            original_filename = file.filename or "uploaded.pdf"
        else:
            raise HTTPException(status_code=400, detail="Must provide a PDF file or select use_sample_pdf=true")

        if len(source_bytes) == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        # Save source PDF in isolated FileStore
        rel_key, checksum, _ = filestore.save_source_pdf(
            project_id=project_id,
            source_path_or_bytes=source_bytes,
            original_filename=original_filename,
        )

        name = project_name or f"Inspection - {original_filename}"
        project = repo.create_project(
            project_id=project_id,
            name=name,
            source_filename=original_filename,
            source_storage_key=rel_key,
            source_checksum=checksum,
            language_variant="en-GB",
            status="extracting"
        )

        # Parse target page numbers if provided
        target_page_numbers: Optional[List[int]] = None
        if pages and pages.strip():
            try:
                # Handle comma-separated and space-separated
                cleaned_pages = pages.replace(",", " ")
                target_page_numbers = [int(p) for p in cleaned_pages.split() if p.isdigit()]
            except ValueError:
                target_page_numbers = None

        source_abs_path = str(filestore.resolve_path(project_id, rel_key))

        try:
            extraction_res = engine.process_pdf(
                pdf_path=source_abs_path,
                page_numbers=target_page_numbers,
                output_images_dir=None # Keep extraction purely memory to FileStore
            )

            # Persist each extracted image into FileStore and Database
            for img in extraction_res.images:
                img_key, img_checksum, _ = filestore.save_image(
                    project_id=project_id,
                    image_id=img.image_id,
                    image_bytes=img.image_bytes,
                    ext=img.image_format
                )

                repo.create_image_asset(
                    image_id=img.image_id,
                    project_id=project_id,
                    page_number=img.page_number,
                    xref=img.xref,
                    placement_index=img.placement_index,
                    bbox=img.bbox.to_dict(),
                    original_caption=img.caption,
                    caption_position=img.caption_position,
                    needs_caption_review=img.needs_caption_review,
                    review_reasons=img.review_reasons,
                    suggested_filename=img.suggested_filename,
                    file_storage_key=img_key,
                    file_checksum=img_checksum,
                    image_format=img.image_format,
                    processing_status="ready_for_review"
                )

            repo.update_project_status(project_id, status="ready_for_review")

        except Exception as e:
            repo.update_project_status(project_id, status="failed")
            raise HTTPException(status_code=500, detail=f"Extraction failure: {repr(e)}")

        saved_images = repo.list_project_images(project_id)

        # Include candidate diagnostic details for frontend inspection
        img_dict_map = {img.image_id: [c.to_dict() for c in img.caption_candidates] for img in extraction_res.images}
        for item in saved_images:
            item["caption_candidates"] = img_dict_map.get(item["id"], [])

        return {
            "project_id": project_id,
            "project_name": name,
            "source_filename": original_filename,
            "pages_processed": extraction_res.pages_processed,
            "stats": {
                "total_images": len(saved_images),
                "captions_found": sum(1 for img in saved_images if bool(img["original_caption"])),
                "needs_review": sum(1 for img in saved_images if img["needs_caption_review"]),
                "matched": sum(1 for img in saved_images if bool(img["original_caption"]) and not img["needs_caption_review"]),
            },
            "images": saved_images
        }

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.web.app:app", host="127.0.0.1", port=8000, reload=True)
