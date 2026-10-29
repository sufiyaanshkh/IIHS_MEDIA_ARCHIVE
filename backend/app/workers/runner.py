"""Persistent polling worker for local media ingestion.

Runs as a separate container/process. Jobs and per-file idempotency are stored in PostgreSQL.
"""
import hashlib
import logging
import os
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db import Base, SessionLocal, engine
from app.models import Asset, IngestJob
from app.services.media import (
    SUPPORTED_EXTENSIONS, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS,
    sha256_file, file_type, image_metadata, create_thumbnail, video_metadata, extract_pdf_text,
)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("archive-worker")


def process_file(db, job: IngestJob, source: Path, path: Path) -> str:
    digest = sha256_file(path)
    existing = db.scalar(select(Asset).where(Asset.sha256 == digest))
    if existing:
        return "skipped"

    relative = str(path.relative_to(source))
    mime = file_type(path)
    width = height = None
    duration = None
    metadata = {"suffix": path.suffix.lower(), "modified_timestamp": path.stat().st_mtime}
    extracted_text = None
    thumb_rel = None

    if path.suffix.lower() in IMAGE_EXTENSIONS:
        metadata.update(image_metadata(path))
        width, height = metadata.get("width"), metadata.get("height")
        if job.options_json.get("generate_thumbnails", True):
            thumb = settings.derivatives_root / "thumbnails" / f"{digest}.webp"
            try:
                create_thumbnail(path, thumb)
                thumb_rel = str(thumb.relative_to(settings.derivatives_root))
            except Exception as exc:
                log.warning("thumbnail failed for %s: %s", relative, exc)
    elif path.suffix.lower() in VIDEO_EXTENSIONS:
        metadata.update(video_metadata(path))
        duration = metadata.get("duration_seconds")
        streams = metadata.get("streams") or []
        if streams:
            width, height = streams[0].get("width"), streams[0].get("height")
        # Frame sampling is intentionally bounded and is the next media-worker extension.
        metadata["frame_sampling"] = "not_yet_enabled"
    elif path.suffix.lower() == ".pdf":
        try:
            extracted_text = extract_pdf_text(path)
        except Exception as exc:
            metadata["text_extraction_error"] = type(exc).__name__

    asset = Asset(
        sha256=digest, original_name=path.name, relative_path=relative, media_type=mime,
        size_bytes=path.stat().st_size, width=width, height=height, duration_seconds=duration,
        source_id=job.source_ref, metadata_json=metadata, thumbnail_path=thumb_rel,
        extracted_text=extracted_text, status="indexed"
    )
    db.add(asset)
    try:
        db.commit()
        return "processed"
    except IntegrityError:
        db.rollback()
        return "skipped"


def process_job(job_id: str) -> None:
    with SessionLocal() as db:
        job = db.get(IngestJob, job_id)
        if not job:
            return
        source = Path(job.source_ref).resolve()
        if not source.is_dir():
            job.status = "failed"
            job.error = "Configured source directory is missing or unavailable"
            db.commit()
            return
        job.status = "processing"
        db.commit()
        recursive = bool(job.options_json.get("recursive", True))
        iterator = source.rglob("*") if recursive else source.glob("*")
        job.discovered = 0
        db.commit()
        for path in iterator:
            if not path.is_file():
                continue
            try:
                if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    job.skipped += 1
                    job.discovered += 1
                    db.commit()
                    continue
                outcome = process_file(db, job, source, path)
                job.discovered += 1
                if outcome == "processed":
                    job.processed += 1
                else:
                    job.skipped += 1
                job.updated_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
                db.commit()
            except Exception as exc:
                db.rollback()
                job = db.get(IngestJob, job_id)
                job.failed += 1
                job.error = f"One or more files failed; latest error type: {type(exc).__name__}"
                job.discovered += 1
                db.commit()
                log.exception("failed processing file %s", path)
        job = db.get(IngestJob, job_id)
        job.status = "completed_with_errors" if job.failed else "completed"
        db.commit()


def main() -> None:
    for folder in (settings.storage_root, settings.derivatives_root, settings.people_root):
        folder.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    log.info("Worker started; polling durable ingest jobs")
    while True:
        try:
            with SessionLocal() as db:
                job = db.scalar(select(IngestJob).where(IngestJob.status == "queued").order_by(IngestJob.created_at).limit(1))
                if job:
                    process_job(job.id)
                    continue
        except Exception:
            log.exception("worker loop error")
        time.sleep(2)


if __name__ == "__main__":
    main()
