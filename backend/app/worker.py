import hashlib
import logging
import os
import shutil
import threading
import time
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select, text

from app.config import settings
from app.database import SessionLocal
from app.models import AssetEmbedding, AuditEvent, IngestionJob, JobCandidate, JobState, MediaAsset, SourceDefinition, VideoFrame
from app.services.embeddings import image_embedding, text_embeddings, transcribe_audio
from app.services.document_readers import SUPPORTED_DOCUMENT_EXTENSIONS, extract_document_text
from app.services.media import MediaValidationError, copy_original, extract_audio, extract_image_text, image_thumbnail, inspect_media, resolve_under_root, sha256_file, video_frame

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("iihs.worker")


def _claim_job():
    now = datetime.now(timezone.utc)
    with SessionLocal.begin() as db:
        stale_before = now - timedelta(minutes=3)
        stale = db.scalars(select(IngestionJob).where(IngestionJob.state.in_([JobState.running.value, JobState.cancel_requested.value]), (IngestionJob.heartbeat_at.is_(None) | (IngestionJob.heartbeat_at < stale_before))).with_for_update(skip_locked=True)).all()
        for job in stale:
            job.state = JobState.cancelled.value if job.cancel_requested else JobState.queued.value
            job.heartbeat_at = None
        job = db.scalar(select(IngestionJob).where(IngestionJob.state == JobState.queued.value).order_by(IngestionJob.created_at).with_for_update(skip_locked=True).limit(1))
        if job:
            job.state = JobState.running.value
            job.heartbeat_at = now
            return job.id
    return None


def _job(job_id):
    with SessionLocal() as db:
        return db.get(IngestionJob, job_id)


def _start_heartbeat(job_id):
    stop = threading.Event()

    def pulse():
        while not stop.wait(30):
            with SessionLocal.begin() as db:
                job = db.get(IngestionJob, job_id)
                if job and job.state in {JobState.running.value, JobState.cancel_requested.value}:
                    job.heartbeat_at = datetime.now(timezone.utc)

    thread = threading.Thread(target=pulse, daemon=True)
    thread.start()
    return stop, thread


def _resolve_source(db, source_id):
    source = db.get(SourceDefinition, source_id)
    if source and source.name == "Browser file import":
        root = Path(source.root_path).resolve(strict=True)
        if root.parent != (settings.storage_root / "imports").resolve() or not root.is_dir():
            raise MediaValidationError("uploaded import source is unavailable")
        return root
    available = {item[0]: item[1] for item in _source_roots()}
    if source is None or not source.enabled or source.id not in available:
        raise MediaValidationError("approved source is unavailable")
    root = available[source.id].resolve(strict=True)
    if Path(source.root_path).resolve(strict=True) != root:
        raise MediaValidationError("approved source configuration changed")
    if not root.is_dir():
        raise MediaValidationError("approved source is unavailable")
    return root


def _source_roots():
    roots = [(settings.demo_media_root, True)] if settings.demo_mode else [(path, False) for path in settings.approved_source_roots]
    return [(str(uuid.uuid5(uuid.NAMESPACE_URL, f"iihs-source:{path.resolve()}")), path) for path, _demo in roots if path.is_dir()]


def _walk(root: Path, limit: int):
    discovered = []
    traversal_errors = 0
    truncated = False

    def on_error(_error):
        nonlocal traversal_errors
        traversal_errors += 1

    for directory, names, filenames in os.walk(root, followlinks=False, onerror=on_error):
        names[:] = sorted(name for name in names if not (Path(directory) / name).is_symlink())
        for name in sorted(filenames):
            candidate = Path(directory) / name
            if candidate.is_symlink():
                continue
            if candidate.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".mp4", ".mov", ".m4v"}:
                continue
            if len(discovered) == limit:
                truncated = True
                break
            discovered.append(candidate)
        if truncated:
            break
    return discovered, traversal_errors, truncated


def _candidate_path(root: Path, key: str):
    return resolve_under_root(root, key)


def _run_preview(job_id: str):
    with SessionLocal() as db:
        job = db.get(IngestionJob, job_id)
        if not job:
            return
        try:
            root = _resolve_source(db, job.source_id)
        except (MediaValidationError, OSError):
            job.state = JobState.failed.value
            job.error_summary = "The approved source is unavailable."
            db.commit()
            return
        limit = min(job.max_files, settings.max_files_per_job) if settings.max_files_per_job else job.max_files
    paths, traversal_errors, truncated = _walk(root, limit)
    counts = {"ready": 0, "duplicate": 0, "invalid": 0}
    seen_hashes = set()
    for index, path in enumerate(paths, 1):
        job = _job(job_id)
        if not job or job.cancel_requested:
            _finish_cancelled(job_id)
            return
        relative = path.relative_to(root).as_posix()
        try:
            safe_path = _candidate_path(root, relative)
            metadata = inspect_media(safe_path)
            digest = sha256_file(safe_path)
            with SessionLocal.begin() as db:
                job = db.get(IngestionJob, job_id)
                if not job or job.cancel_requested:
                    continue
                duplicate = digest in seen_hashes or db.scalar(select(MediaAsset.id).where(MediaAsset.sha256 == digest))
                seen_hashes.add(digest)
                state = "duplicate" if duplicate else "ready"
                item = db.scalar(select(JobCandidate).where(JobCandidate.job_id == job_id, JobCandidate.source_relative_key == relative))
                values = dict(original_filename=path.name, media_type=metadata["media_type"], size_bytes=metadata["size"], sha256=digest, state=state, message="Already catalogued or duplicated in this proposal." if duplicate else None, metadata_json=metadata)
                if item:
                    for key, value in values.items():
                        setattr(item, key, value)
                else:
                    db.add(JobCandidate(job_id=job_id, source_relative_key=relative, **values))
                job.progress_done = index
                job.progress_total = len(paths)
                job.heartbeat_at = datetime.now(timezone.utc)
        except (MediaValidationError, OSError, ValueError):
            with SessionLocal.begin() as db:
                job = db.get(IngestionJob, job_id)
                item = db.scalar(select(JobCandidate).where(JobCandidate.job_id == job_id, JobCandidate.source_relative_key == relative))
                values = dict(original_filename=path.name, media_type="unknown", size_bytes=0, sha256="", state="invalid", message="File could not be validated.", metadata_json={})
                if item:
                    for key, value in values.items():
                        setattr(item, key, value)
                else:
                    db.add(JobCandidate(job_id=job_id, source_relative_key=relative, **values))
                if job:
                    job.progress_done = index
                    job.progress_total = len(paths)
                    job.heartbeat_at = datetime.now(timezone.utc)
    with SessionLocal.begin() as db:
        job = db.get(IngestionJob, job_id)
        if not job:
            return
        if job.cancel_requested or job.state == JobState.cancel_requested.value:
            job.state = JobState.cancelled.value
            job.heartbeat_at = None
            db.add(AuditEvent(actor_id=job.requested_by, action="ingestion_cancelled", target_type="job", target_id=job.id))
            return
        candidates = db.scalars(select(JobCandidate).where(JobCandidate.job_id == job_id)).all()
        for item in candidates:
            counts[item.state] = counts.get(item.state, 0) + 1
        job.progress_done = len(paths)
        job.progress_total = len(paths)
        job.summary = {"discovered": len(paths), **counts, "traversal_errors": traversal_errors, "scope_truncated": truncated, "scope_limit": limit}
        job.state = JobState.preview_ready.value
        job.heartbeat_at = None
        db.add(AuditEvent(actor_id=job.requested_by, action="ingestion_preview_completed", target_type="job", target_id=job.id, detail=job.summary))


def _store_video_frames(db, asset, original: Path, metadata, created_paths):
    duration = metadata["duration_ms"] or 0
    interval = settings.video_frame_interval_seconds * 1000
    timestamps = [step * interval for step in range(settings.video_frame_max) if step * interval < duration]
    failed = 0
    embeddings = []
    for timestamp in timestamps:
        frame_id = str(uuid.uuid4())
        key = f"{asset.sha256[:2]}/{asset.sha256}-frame-{timestamp}.webp"
        try:
            frame_path = video_frame(original, timestamp, key)
            created_paths.append(frame_path)
            frame = VideoFrame(id=frame_id, asset_id=asset.id, timestamp_ms=timestamp, storage_key=key)
            db.add(frame)
            embeddings.append((frame, frame_path))
            if timestamp == 0:
                asset.thumbnail_key = key
        except MediaValidationError:
            failed += 1
    if failed:
        asset.metadata_json = asset.metadata_json | {"frame_derivative_failures": failed}
    return failed, embeddings


def _store_text_embeddings(db, asset, text: str, modality: str):
    from app.services.search_segments import chunk_text
    chunks = chunk_text(text, max_chars=settings.document_chunk_chars, overlap=settings.document_chunk_overlap)
    for offset in range(0, len(chunks), 32):
        prompts = [settings.embedding_document_prompt.format(title=asset.original_filename, text=chunk) for chunk in chunks[offset:offset + 32]]
        vectors = text_embeddings(prompts)
        for vector, model_id, model_revision in vectors:
            db.add(AssetEmbedding(asset_id=asset.id, embedding=vector, modality=modality, model_id=model_id, model_revision=model_revision))



def _run_ingest(job_id: str):
    with SessionLocal() as db:
        job = db.get(IngestionJob, job_id)
        if not job:
            return
        try:
            root = _resolve_source(db, job.source_id)
            candidates = db.scalars(select(JobCandidate).where(JobCandidate.job_id == job_id, JobCandidate.state == "ready").order_by(JobCandidate.source_relative_key)).all()
            candidate_ids = [candidate.id for candidate in candidates]
        except (MediaValidationError, OSError):
            job.state = JobState.failed.value
            job.error_summary = "The approved source is unavailable."
            db.commit()
            return
        job.progress_total = len(candidate_ids)
        db.commit()
    succeeded = failed = derivative_failed = 0
    indexed = 0
    for index, candidate_id in enumerate(candidate_ids, 1):
        current = _job(job_id)
        if not current or current.cancel_requested:
            _finish_cancelled(job_id)
            return
        stored_path = None
        created_frames = []
        created_derivatives = []
        item_derivative_failures = 0
        with SessionLocal() as db:
            candidate = db.get(JobCandidate, candidate_id)
            job = db.get(IngestionJob, job_id)
            source_id = job.source_id if job else ""
            source = db.get(SourceDefinition, source_id) if source_id else None
            try:
                digest_lock = int(candidate.sha256[:16], 16)
                if digest_lock >= 2**63:
                    digest_lock -= 2**64
                db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": digest_lock})
                existing_asset = db.scalar(select(MediaAsset.id).where(MediaAsset.sha256 == candidate.sha256))
                if existing_asset:
                    candidate.state = "duplicate"
                    candidate.message = "Matching content was ingested by another job."
                    job.progress_done = index
                    job.heartbeat_at = datetime.now(timezone.utc)
                    db.commit()
                    continue
                root = _resolve_source(db, source_id)
                path = _candidate_path(root, candidate.source_relative_key)
                metadata = inspect_media(path)
                if sha256_file(path) != candidate.sha256:
                    raise MediaValidationError("source changed after preview")
                asset_id = str(uuid.uuid4())
                suffix = path.suffix.lower()
                storage_key = f"{candidate.sha256[:2]}/{candidate.sha256}{suffix}"
                stored_path = copy_original(path, storage_key)
                if sha256_file(stored_path) != candidate.sha256:
                    raise MediaValidationError("source changed while it was being copied")
                asset = MediaAsset(id=asset_id, source_id=source.id, source_relative_key=candidate.source_relative_key, original_filename=candidate.original_filename, media_type=metadata["media_type"], detected_mime=metadata["mime"], size_bytes=metadata["size"], sha256=candidate.sha256, storage_key=storage_key, width=metadata.get("width"), height=metadata.get("height"), duration_ms=metadata.get("duration_ms"), metadata_json={"ingestion_job_id": job_id})
                db.add(asset)
                if asset.media_type == "image":
                    try:
                        thumbnail_key = f"{candidate.sha256[:2]}/{candidate.sha256}.thumb.webp"
                        created_derivatives.append(image_thumbnail(stored_path, thumbnail_key))
                        asset.thumbnail_key = thumbnail_key
                    except MediaValidationError:
                        asset.metadata_json = asset.metadata_json | {"derivative_status": "thumbnail_unavailable"}
                    embedding_sources = [(None, created_derivatives[-1] if created_derivatives else stored_path)]
                else:
                    frame_failures, embedding_sources = _store_video_frames(db, asset, stored_path, metadata, created_frames)
                    item_derivative_failures += frame_failures
                item_derivative_failures += int(asset.metadata_json.get("derivative_status") == "thumbnail_unavailable")
                db.flush()
                if not embedding_sources:
                    raise MediaValidationError("No image frame was available for semantic indexing")
                for frame, image_path in embedding_sources:
                    vector, model_id, model_revision = image_embedding(image_path)
                    db.add(AssetEmbedding(asset_id=asset.id, frame_id=frame.id if frame else None, embedding=vector, modality="image_frame" if frame else "image", model_id=model_id, model_revision=model_revision))
                if asset.media_type == "image":
                    try:
                        ocr_text = extract_image_text(stored_path)
                        asset.metadata_json = asset.metadata_json | {"text_extraction": {"status": "ready" if ocr_text else "no_text", "text": ocr_text or ""}}
                        if ocr_text:
                            _store_text_embeddings(db, asset, ocr_text, "ocr")
                    except MediaValidationError:
                        asset.metadata_json = asset.metadata_json | {"text_extraction": {"status": "unavailable", "text": ""}}
                elif path.suffix.lower() in SUPPORTED_DOCUMENT_EXTENSIONS:
                    try:
                        extracted = extract_document_text(stored_path)
                        segments = extracted.get("segments", [])
                        for segment in segments:
                            text_value = (segment.get("text") or "").strip()
                            if text_value:
                                _store_text_embeddings(db, asset, text_value, f"document_{segment.get('kind', 'text')}")
                        asset.metadata_json = asset.metadata_json | {
                            "text_extraction": {
                                "status": "ready" if extracted.get("text") else "no_text",
                                "text": extracted.get("text", "")[:500000],
                                "segments": [{"kind": item.get("kind"), "ordinal": item.get("ordinal"), "title": item.get("title")} for item in segments],
                            }
                        }
                    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
                        asset.metadata_json = asset.metadata_json | {"text_extraction": {"status": "unavailable", "text": ""}}
                indexed += 1
                if asset.media_type == "video":
                    try:
                        heartbeat_stop, heartbeat_thread = _start_heartbeat(job_id)
                        try:
                            transcript = transcribe_audio(extract_audio(stored_path, metadata["duration_ms"] or 0))
                        finally:
                            heartbeat_stop.set()
                            heartbeat_thread.join()
                        asset.metadata_json = asset.metadata_json | {"transcription": {"status": "ready", "language": transcript["language"], "text": transcript["text"], "english_translation": transcript["english_translation"], "model_id": transcript["model_id"], "model_revision": transcript["model_revision"]}}
                        if transcript["text"]:
                            _store_text_embeddings(db, asset, transcript["text"], "transcript")
                        if transcript["english_translation"]:
                            _store_text_embeddings(db, asset, transcript["english_translation"], "translation")
                    except MediaValidationError:
                        asset.metadata_json = asset.metadata_json | {"transcription": {"status": "unavailable"}}
                candidate.state = "ingested"
                job.progress_done = index
                job.heartbeat_at = datetime.now(timezone.utc)
                db.commit()
                succeeded += 1
                derivative_failed += item_derivative_failures
            except Exception as error:
                db.rollback()
                if stored_path:
                    stored_path.unlink(missing_ok=True)
                    try:
                        stored_path.parent.rmdir()
                    except OSError:
                        pass
                for frame_path in created_frames:
                    frame_path.unlink(missing_ok=True)
                for derivative_path in created_derivatives:
                    derivative_path.unlink(missing_ok=True)
                storage_stop = isinstance(error, MediaValidationError) and str(error) == "storage capacity reached"
                with SessionLocal.begin() as failed_db:
                    item = failed_db.get(JobCandidate, candidate_id)
                    job = failed_db.get(IngestionJob, job_id)
                    if item:
                        item.state = "ready" if storage_stop else "failed"
                        item.message = "Storage free-space stop threshold reached." if storage_stop else "File could not be copied or registered safely."
                    if job:
                        job.progress_done = index
                        job.heartbeat_at = None if storage_stop else datetime.now(timezone.utc)
                        if storage_stop:
                            job.state = JobState.failed.value
                            job.error_summary = "Storage free-space stop threshold reached."
                            job.summary = {"ingested": succeeded, "failed": failed + 1, "derivative_failures": derivative_failed, "skipped": max(0, job.progress_total - index)}
                            failed_db.add(AuditEvent(actor_id=job.requested_by, action="ingestion_stopped_storage_threshold", target_type="job", target_id=job.id, detail=job.summary))
                failed += 1
                log.warning("A media item failed during an ingestion job (%s, SQLSTATE %s).", type(error).__name__, getattr(getattr(error, "orig", None), "sqlstate", "unknown"))
                if storage_stop:
                    return
    with SessionLocal.begin() as db:
        job = db.get(IngestionJob, job_id)
        if not job:
            return
        job.heartbeat_at = None
        job.summary = {"ingested": succeeded, "indexed": indexed, "failed": failed, "derivative_failures": derivative_failed, "skipped": max(0, job.progress_total - succeeded - failed)}
        job.state = JobState.cancelled.value if job.cancel_requested else JobState.partially_completed.value if failed or derivative_failed else JobState.completed.value
        db.add(AuditEvent(actor_id=job.requested_by, action="ingestion_completed", target_type="job", target_id=job.id, detail=job.summary))
        source = db.get(SourceDefinition, job.source_id)
        if source and source.name == "Browser file import" and not failed:
            shutil.rmtree(source.root_path, ignore_errors=True)


def _finish_cancelled(job_id):
    with SessionLocal.begin() as db:
        job = db.get(IngestionJob, job_id)
        if job:
            job.state = JobState.cancelled.value
            job.heartbeat_at = None
            db.add(AuditEvent(actor_id=job.requested_by, action="ingestion_cancelled", target_type="job", target_id=job.id))


def run_once():
    job_id = _claim_job()
    if not job_id:
        return False
    job = _job(job_id)
    if job:
        if job.kind == "preview":
            _run_preview(job_id)
        elif job.kind == "ingest":
            _run_ingest(job_id)
        else:
            with SessionLocal.begin() as db:
                row = db.get(IngestionJob, job_id)
                if row:
                    row.state = JobState.failed.value
                    row.error_summary = "Unsupported job type."
    return True


def main():
    while True:
        try:
            if not run_once():
                time.sleep(1)
        except Exception:
            log.exception("Worker cycle failed.")
            time.sleep(2)


if __name__ == "__main__":
    main()
