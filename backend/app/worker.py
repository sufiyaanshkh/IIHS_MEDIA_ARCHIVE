import hashlib
import logging
import os
import shutil
import signal
import threading
import time
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, select, text

from app.config import settings
from app.database import SessionLocal
from app.models import (
    AssetEmbedding,
    AuditEvent,
    DetectedArchiveFace,
    IngestionJob,
    JobCandidate,
    JobState,
    MediaAsset,
    SearchSegment,
    SourceDefinition,
    SourceFileRecord,
    VideoFrame,
)
from app.services.contextual_metadata import infer_path_context
from app.services.crawler import ALL_SUPPORTED_EXTENSIONS, crawl_archive_root
from app.services.document_readers import SUPPORTED_DOCUMENT_EXTENSIONS, extract_document_text
from app.services.embeddings import image_embedding, text_embeddings, transcribe_audio
from app.services.face_recognition import is_biometric_governance_authorized, process_archive_asset_faces
from app.services.insights import generate_visual_description
from app.services.media import (
    MediaValidationError,
    copy_original,
    extract_audio,
    extract_image_text,
    image_thumbnail,
    inspect_media,
    resolve_under_root,
    sha256_file,
    video_frame,
)
from app.services.search_segments import (
    build_transcript_windows,
    chunk_text,
    compute_frame_dhash,
    hamming_distance,
    segment_content_hash,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("iihs.worker")
_shutdown_requested = threading.Event()


def _install_signal_handlers():
    def _handle_sig(_signum, _frame):
        log.info("Worker received shutdown signal; finishing active file safely.")
        _shutdown_requested.set()

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                signal.signal(sig, _handle_sig)
            except ValueError:
                pass


def compute_job_telemetry(job: IngestionJob, now: datetime | None = None) -> dict:
    """Compute throughput (files/s, MB/s) and ETA from measured job progress."""
    current_time = now or datetime.now(timezone.utc)
    started = job.started_at
    completed = job.completed_at
    if started and started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if completed and completed.tzinfo is None:
        completed = completed.replace(tzinfo=timezone.utc)

    end_ref = completed if completed else (current_time if started else None)
    elapsed_seconds = max(0.0, (end_ref - started).total_seconds()) if (started and end_ref) else 0.0

    done_files = int(job.processed_files or job.progress_done or 0)
    total_files = int(job.discovered_files or job.progress_total or 0)
    done_bytes = int(job.processed_bytes or 0)
    total_bytes = int(job.total_bytes or 0)

    files_per_sec = round(done_files / elapsed_seconds, 3) if elapsed_seconds >= 0.5 and done_files > 0 else 0.0
    mb_per_sec = round((done_bytes / (1024 * 1024)) / elapsed_seconds, 3) if elapsed_seconds >= 0.5 and done_bytes > 0 else 0.0

    # ETA requires measured throughput (>= 2 processed files or >= 1.5s elapsed with positive progress)
    eta_seconds: float | None = None
    if job.state in {JobState.running.value, JobState.queued.value} and done_files >= 2 and elapsed_seconds >= 0.5:
        if total_bytes > done_bytes and done_bytes > 0:
            bytes_per_sec = done_bytes / elapsed_seconds
            eta_seconds = round((total_bytes - done_bytes) / bytes_per_sec, 1)
        elif total_files > done_files and files_per_sec > 0:
            eta_seconds = round((total_files - done_files) / files_per_sec, 1)
    elif job.state in {JobState.completed.value, JobState.partially_completed.value, JobState.preview_ready.value}:
        eta_seconds = 0.0

    return {
        "discovered_files": total_files,
        "processed_files": done_files,
        "total_bytes": total_bytes,
        "processed_bytes": done_bytes,
        "failed_files": int(job.failed_files or 0),
        "skipped_files": int(job.skipped_files or 0),
        "current_stage": job.current_stage or job.state,
        "started_at": started.isoformat() if started else None,
        "completed_at": completed.isoformat() if completed else None,
        "elapsed_seconds": round(elapsed_seconds, 2),
        "files_per_second": files_per_sec,
        "mb_per_second": mb_per_sec,
        "eta_seconds": eta_seconds,
    }


def _claim_job():
    now = datetime.now(timezone.utc)
    with SessionLocal.begin() as db:
        stale_before = now - timedelta(minutes=3)
        stale = db.scalars(
            select(IngestionJob)
            .where(
                IngestionJob.state.in_([JobState.running.value, JobState.cancel_requested.value]),
                (IngestionJob.heartbeat_at.is_(None) | (IngestionJob.heartbeat_at < stale_before)),
            )
            .with_for_update(skip_locked=True)
        ).all()
        for job in stale:
            job.state = JobState.cancelled.value if job.cancel_requested else JobState.queued.value
            job.current_stage = "cancelled" if job.cancel_requested else "requeued_after_recovery"
            job.heartbeat_at = None
        job = db.scalar(
            select(IngestionJob)
            .where(IngestionJob.state == JobState.queued.value)
            .order_by(IngestionJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job:
            job.state = JobState.running.value
            if job.started_at is None:
                job.started_at = now
            job.current_stage = "initializing"
            job.heartbeat_at = now
            return job.id
    return None


def _job(job_id):
    with SessionLocal() as db:
        return db.get(IngestionJob, job_id)


def _start_heartbeat(job_id, stage_name: str | None = None):
    stop = threading.Event()

    def pulse():
        while not stop.wait(20):
            with SessionLocal.begin() as db:
                job = db.get(IngestionJob, job_id)
                if job and job.state in {JobState.running.value, JobState.cancel_requested.value}:
                    job.heartbeat_at = datetime.now(timezone.utc)
                    if stage_name:
                        job.current_stage = stage_name

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

    if source is None or not source.enabled:
        raise MediaValidationError("approved source is unavailable")

    # 1. Check if source root_path directly exists and is a directory
    try:
        cand_path = Path(source.root_path)
        if cand_path.is_dir():
            return cand_path.resolve()
    except Exception:
        pass

    # 2. Check mounted approved sources (e.g. inside Docker container)
    for mount_cand in (
        Path("/mnt/approved_sources/IIHS_External_HDD"),
        *settings.approved_source_roots,
    ):
        try:
            if mount_cand.is_dir():
                return mount_cand.resolve()
        except Exception:
            pass

    if settings.demo_mode and settings.demo_media_root.is_dir():
        return settings.demo_media_root.resolve()

    raise MediaValidationError("approved source is unavailable")


def _source_roots():
    roots = [(settings.demo_media_root, True)] if settings.demo_mode else [(path, False) for path in settings.approved_source_roots]
    return [(str(uuid.uuid5(uuid.NAMESPACE_URL, f"iihs-source:{path.resolve()}")), path) for path, _demo in roots if path.is_dir()]


def _walk(root: Path, limit: int, resume_after_key: str | None = None):
    crawl = crawl_archive_root(root, limit=limit, resume_after_key=resume_after_key, allowed_extensions=ALL_SUPPORTED_EXTENSIONS)
    paths = [item.path for item in crawl.items]
    return paths, crawl.traversal_errors, crawl.truncated, crawl


def _candidate_path(root: Path, key: str):
    return resolve_under_root(root, key)


def _run_preview(job_id: str):
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        job = db.get(IngestionJob, job_id)
        if not job:
            return
        try:
            root = _resolve_source(db, job.source_id)
            source_id = job.source_id
        except (MediaValidationError, OSError):
            job.state = JobState.failed.value
            job.current_stage = "failed"
            job.completed_at = now
            job.error_summary = "The approved source is unavailable."
            db.commit()
            return
        limit = min(job.max_files, settings.max_files_per_job) if settings.max_files_per_job else job.max_files
        resume_cursor = (job.summary or {}).get("last_crawled_key")
        job.current_stage = "crawling"
        if not job.started_at:
            job.started_at = now
        db.commit()

    paths, traversal_errors, truncated, crawl = _walk(root, limit, resume_after_key=resume_cursor)
    counts = {"ready": 0, "duplicate": 0, "invalid": 0, "skipped": 0, "moved": 0, "modified": 0}
    seen_hashes: set[str] = set()
    processed_bytes = 0

    with SessionLocal.begin() as db:
        job = db.get(IngestionJob, job_id)
        if job:
            job.discovered_files = len(crawl.items)
            job.progress_total = len(crawl.items)
            job.total_bytes = crawl.total_bytes
            job.current_stage = "validating"

    for index, discovered in enumerate(crawl.items, 1):
        if _shutdown_requested.is_set():
            return
        job = _job(job_id)
        if not job or job.cancel_requested:
            _finish_cancelled(job_id)
            return
        path = discovered.path
        relative = discovered.relative_key
        try:
            safe_path = _candidate_path(root, relative)
            with SessionLocal.begin() as db:
                job = db.get(IngestionJob, job_id)
                if not job or job.cancel_requested:
                    continue

                # Incremental fast-skip check: if SourceFileRecord exists with identical size & mtime_ns and active asset
                existing_record = db.scalar(
                    select(SourceFileRecord).where(
                        SourceFileRecord.source_id == source_id,
                        SourceFileRecord.source_relative_key == relative,
                    )
                )
                if (
                    existing_record
                    and existing_record.status == "active"
                    and existing_record.size_bytes == discovered.size_bytes
                    and existing_record.mtime_ns == discovered.mtime_ns
                    and existing_record.asset_id
                    and db.get(MediaAsset, existing_record.asset_id) is not None
                ):
                    state = "skipped"
                    message = "Unchanged since previous scan (size and mtime match)."
                    digest = existing_record.sha256
                    metadata = {
                        "media_type": existing_record.media_type,
                        "size": existing_record.size_bytes,
                        "mtime_ns": discovered.mtime_ns,
                        "device_inode": discovered.device_inode,
                        "incremental_action": "unchanged",
                        "context": discovered.context,
                    }
                    existing_record.last_seen_job_id = job_id
                    existing_record.last_seen_at = datetime.now(timezone.utc)
                else:
                    metadata = inspect_media(safe_path)
                    digest = sha256_file(safe_path)
                    metadata["mtime_ns"] = discovered.mtime_ns
                    metadata["device_inode"] = discovered.device_inode
                    metadata["context"] = discovered.context

                    existing_asset_by_hash = db.scalar(select(MediaAsset).where(MediaAsset.sha256 == digest))
                    if existing_record and existing_record.sha256 != digest:
                        state = "ready"
                        metadata["incremental_action"] = "modified"
                        message = "Modified file detected; will update and re-index."
                    elif existing_asset_by_hash is not None:
                        # Check if the file was moved/renamed within the same source root
                        old_rel = existing_asset_by_hash.source_relative_key
                        old_exists = False
                        if existing_asset_by_hash.source_id == source_id and old_rel and old_rel != relative:
                            try:
                                _candidate_path(root, old_rel)
                                old_exists = True
                            except (MediaValidationError, OSError):
                                old_exists = False
                        if (
                            existing_asset_by_hash.source_id == source_id
                            and old_rel
                            and old_rel != relative
                            and not old_exists
                            and digest not in seen_hashes
                        ):
                            state = "ready"
                            metadata["incremental_action"] = "moved"
                            metadata["moved_from"] = old_rel
                            message = f"Moved/renamed from {old_rel}; will update path without re-embedding."
                        else:
                            state = "duplicate"
                            metadata["incremental_action"] = "duplicate"
                            message = "Already catalogued or duplicated in this proposal."
                    elif digest in seen_hashes:
                        state = "duplicate"
                        metadata["incremental_action"] = "duplicate_in_batch"
                        message = "Duplicate file within this scan."
                    else:
                        state = "ready"
                        metadata["incremental_action"] = "new"
                        message = None

                seen_hashes.add(digest)
                processed_bytes += discovered.size_bytes
                item = db.scalar(select(JobCandidate).where(JobCandidate.job_id == job_id, JobCandidate.source_relative_key == relative))
                values = dict(
                    original_filename=path.name,
                    media_type=metadata["media_type"],
                    size_bytes=discovered.size_bytes,
                    sha256=digest,
                    state=state,
                    message=message,
                    metadata_json=metadata,
                )
                if item:
                    for key, value in values.items():
                        setattr(item, key, value)
                else:
                    db.add(JobCandidate(job_id=job_id, source_relative_key=relative, **values))
                job.progress_done = index
                job.processed_files = index
                job.processed_bytes = processed_bytes
                if state == "skipped":
                    job.skipped_files = int(job.skipped_files or 0) + 1
                job.summary = (job.summary or {}) | {"last_crawled_key": relative}
                job.heartbeat_at = datetime.now(timezone.utc)
        except (MediaValidationError, OSError, ValueError):
            with SessionLocal.begin() as db:
                job = db.get(IngestionJob, job_id)
                item = db.scalar(select(JobCandidate).where(JobCandidate.job_id == job_id, JobCandidate.source_relative_key == relative))
                values = dict(
                    original_filename=path.name,
                    media_type="unknown",
                    size_bytes=discovered.size_bytes,
                    sha256="",
                    state="invalid",
                    message="File could not be validated.",
                    metadata_json={"context": discovered.context},
                )
                if item:
                    for key, value in values.items():
                        setattr(item, key, value)
                else:
                    db.add(JobCandidate(job_id=job_id, source_relative_key=relative, **values))
                if job:
                    job.progress_done = index
                    job.processed_files = index
                    job.failed_files = int(job.failed_files or 0) + 1
                    job.heartbeat_at = datetime.now(timezone.utc)

    with SessionLocal.begin() as db:
        job = db.get(IngestionJob, job_id)
        if not job:
            return
        if job.cancel_requested or job.state == JobState.cancel_requested.value:
            job.state = JobState.cancelled.value
            job.current_stage = "cancelled"
            job.completed_at = datetime.now(timezone.utc)
            job.heartbeat_at = None
            db.add(AuditEvent(actor_id=job.requested_by, action="ingestion_cancelled", target_type="job", target_id=job.id))
            return
        candidates = db.scalars(select(JobCandidate).where(JobCandidate.job_id == job_id)).all()
        for item in candidates:
            counts[item.state] = counts.get(item.state, 0) + 1

        # Reconcile deleted files ONLY if this was a complete, non-truncated scan without traversal errors
        reconciled_missing = 0
        if not truncated and traversal_errors == 0:
            seen_rel_keys = {c.relative_key for c in crawl.items}
            active_records = db.scalars(
                select(SourceFileRecord).where(
                    SourceFileRecord.source_id == source_id,
                    SourceFileRecord.status == "active",
                )
            ).all()
            for rec in active_records:
                if rec.source_relative_key not in seen_rel_keys:
                    rec.status = "missing_from_source"
                    rec.updated_at = datetime.now(timezone.utc)
                    reconciled_missing += 1

        job.progress_done = len(crawl.items)
        job.processed_files = len(crawl.items)
        job.progress_total = len(crawl.items)
        job.completed_at = datetime.now(timezone.utc)
        job.current_stage = "preview_ready"
        telemetry = compute_job_telemetry(job)
        job.summary = {
            "discovered": len(crawl.items),
            **counts,
            "reconciled_missing": reconciled_missing,
            "traversal_errors": traversal_errors,
            "skipped_unsupported": crawl.skipped_unsupported,
            "skipped_unsafe": crawl.skipped_unsafe,
            "scope_truncated": truncated,
            "scope_limit": limit,
            "total_bytes": crawl.total_bytes,
            "elapsed_seconds": telemetry["elapsed_seconds"],
        }
        job.state = JobState.preview_ready.value
        job.heartbeat_at = None
        db.add(AuditEvent(actor_id=job.requested_by, action="ingestion_preview_completed", target_type="job", target_id=job.id, detail=job.summary))


def _store_video_frames(db, asset, original: Path, metadata, created_paths):
    duration = metadata["duration_ms"] or 0
    interval = settings.video_frame_interval_seconds * 1000
    timestamps = [step * interval for step in range(settings.video_frame_max) if step * interval < duration]
    if not timestamps and duration > 0:
        timestamps = [0]
    failed = 0
    deduplicated = 0
    embeddings = []
    last_dhash: int | None = None
    dedup_threshold = int(getattr(settings, "video_frame_dedup_threshold", 4))

    for timestamp in timestamps:
        frame_id = str(uuid.uuid4())
        key = f"{asset.sha256[:2]}/{asset.sha256}-frame-{timestamp}.webp"
        try:
            frame_path = video_frame(original, timestamp, key)
            if timestamp == 0:
                asset.thumbnail_key = key

            # Frame deduplication: skip near-identical consecutive static frames (except frame 0)
            if timestamp > 0 and dedup_threshold >= 0:
                try:
                    current_dhash = compute_frame_dhash(frame_path)
                    if last_dhash is not None and hamming_distance(current_dhash, last_dhash) <= dedup_threshold:
                        frame_path.unlink(missing_ok=True)
                        deduplicated += 1
                        continue
                    last_dhash = current_dhash
                except Exception:
                    pass
            elif timestamp == 0:
                try:
                    last_dhash = compute_frame_dhash(frame_path)
                except Exception:
                    last_dhash = None

            created_paths.append(frame_path)
            frame = VideoFrame(id=frame_id, asset_id=asset.id, timestamp_ms=timestamp, storage_key=key)
            db.add(frame)
            embeddings.append((frame, frame_path))
        except MediaValidationError:
            failed += 1
    extra_meta = {}
    if failed:
        extra_meta["frame_derivative_failures"] = failed
    if deduplicated:
        extra_meta["deduplicated_static_frames"] = deduplicated
    if extra_meta:
        asset.metadata_json = (asset.metadata_json or {}) | extra_meta
    return failed, embeddings


def _add_segment_and_embedding(
    db,
    asset: MediaAsset,
    *,
    segment_type: str,
    modality: str,
    ordinal: int,
    text_content: str | None = None,
    title: str | None = None,
    vector: list[float] | None = None,
    model_id: str | None = None,
    model_revision: str | None = None,
    frame_id: str | None = None,
    page_number: int | None = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
    metadata_json: dict | None = None,
):
    c_hash = segment_content_hash(segment_type, text_content or title or str(frame_id or asset.sha256), ordinal)
    seg = SearchSegment(
        asset_id=asset.id,
        frame_id=frame_id,
        segment_type=segment_type,
        modality=modality,
        title=title or asset.original_filename,
        text_content=text_content,
        embedding=vector,
        model_id=model_id,
        model_revision=model_revision,
        segment_ordinal=ordinal,
        page_number=page_number,
        start_ms=start_ms,
        end_ms=end_ms,
        original_file_reference=asset.source_relative_key or asset.original_filename,
        content_hash=c_hash,
        metadata_json=metadata_json or {},
    )
    db.add(seg)
    if vector is not None and model_id and model_revision:
        # Map modality to allowed AssetEmbedding constraint values
        allowed_emb_modalities = {
            "image", "image_frame", "ocr", "transcript", "translation", "context",
            "document_text", "document_page", "document_document", "document_sheet",
            "document_slide", "document_chunk", "audio_transcript", "visual_description",
        }
        emb_modality = modality if modality in allowed_emb_modalities else "document_text"
        db.add(
            AssetEmbedding(
                asset_id=asset.id,
                frame_id=frame_id,
                embedding=vector,
                modality=emb_modality,
                model_id=model_id,
                model_revision=model_revision,
            )
        )


def _store_text_embeddings(
    db,
    asset: MediaAsset,
    text_val: str,
    modality: str,
    *,
    segment_type: str | None = None,
    page_number: int | None = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
    title: str | None = None,
    base_ordinal: int = 1,
):
    resolved_seg_type = segment_type or (
        "contextual_metadata" if modality == "context"
        else "ocr_text" if modality == "ocr"
        else "audio_transcript_window" if modality in {"transcript", "translation", "audio_transcript"}
        else "pdf_page" if modality == "document_page"
        else "spreadsheet_sheet" if modality == "document_sheet"
        else "presentation_slide" if modality == "document_slide"
        else "visual_description" if modality == "visual_description"
        else "office_chunk"
    )
    chunks = chunk_text(text_val, max_chars=settings.document_chunk_chars, overlap=settings.document_chunk_overlap)
    batch_size = max(1, int(getattr(settings, "embedding_batch_size", 32)))
    ordinal = base_ordinal
    for offset in range(0, len(chunks), batch_size):
        batch_chunks = chunks[offset:offset + batch_size]
        prompts = [
            settings.embedding_document_prompt.format(title=title or asset.original_filename, text=chunk)
            for chunk in batch_chunks
        ]
        try:
            vectors = text_embeddings(prompts)
        except MediaValidationError:
            vectors = [(None, None, None) for _ in batch_chunks]
        for chunk_str, (vector, model_id, model_revision) in zip(batch_chunks, vectors):
            _add_segment_and_embedding(
                db,
                asset,
                segment_type=resolved_seg_type,
                modality=modality,
                ordinal=ordinal,
                text_content=chunk_str,
                title=title or asset.original_filename,
                vector=vector,
                model_id=model_id,
                model_revision=model_revision,
                page_number=page_number,
                start_ms=start_ms,
                end_ms=end_ms,
                metadata_json={"is_visual_claim": resolved_seg_type == "visual_description"},
            )
            ordinal += 1
    return ordinal


def _upsert_source_file_record(
    db,
    *,
    source_id: str | None,
    relative_key: str | None,
    asset: MediaAsset,
    size_bytes: int,
    mtime_ns: int,
    device_inode: str | None,
    job_id: str,
):
    if not source_id or not relative_key:
        return
    record = db.scalar(
        select(SourceFileRecord).where(
            SourceFileRecord.source_id == source_id,
            SourceFileRecord.source_relative_key == relative_key,
        )
    )
    now = datetime.now(timezone.utc)
    if record is None:
        record = SourceFileRecord(
            source_id=source_id,
            source_relative_key=relative_key,
            asset_id=asset.id,
            original_filename=asset.original_filename,
            media_type=asset.media_type,
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            device_inode=device_inode,
            sha256=asset.sha256,
            status="active",
            last_seen_job_id=job_id,
            last_seen_at=now,
        )
        db.add(record)
    else:
        record.asset_id = asset.id
        record.original_filename = asset.original_filename
        record.media_type = asset.media_type
        record.size_bytes = size_bytes
        record.mtime_ns = mtime_ns
        record.device_inode = device_inode
        record.sha256 = asset.sha256
        record.status = "active"
        record.last_seen_job_id = job_id
        record.last_seen_at = now
        record.updated_at = now


def _run_ingest(job_id: str):
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        job = db.get(IngestionJob, job_id)
        if not job:
            return
        try:
            root = _resolve_source(db, job.source_id)
            all_candidates = db.scalars(
                select(JobCandidate)
                .where(JobCandidate.job_id == job_id)
                .order_by(JobCandidate.source_relative_key)
            ).all()
            ready_candidates = [c for c in all_candidates if c.state == "ready"]
            already_ingested = sum(1 for c in all_candidates if c.state == "ingested")
            already_skipped = sum(1 for c in all_candidates if c.state in {"skipped", "duplicate"})
            already_failed = sum(1 for c in all_candidates if c.state == "failed")
            already_bytes = sum(int(c.size_bytes or 0) for c in all_candidates if c.state in {"ingested", "skipped", "duplicate"})
            total_candidate_bytes = sum(int(c.size_bytes or 0) for c in all_candidates if c.state in {"ready", "ingested", "skipped", "duplicate"})
            candidate_ids = [candidate.id for candidate in ready_candidates]
        except (MediaValidationError, OSError):
            job.state = JobState.failed.value
            job.current_stage = "failed"
            job.completed_at = now
            job.error_summary = "The approved source is unavailable."
            db.commit()
            return
        total_items = len(ready_candidates) + already_ingested
        job.progress_total = total_items
        job.discovered_files = len(all_candidates)
        job.total_bytes = total_candidate_bytes
        job.processed_bytes = already_bytes
        job.processed_files = already_ingested
        job.skipped_files = already_skipped
        job.failed_files = already_failed
        if not job.started_at:
            job.started_at = now
        job.current_stage = "ingesting"
        db.commit()

    succeeded = already_ingested
    failed = already_failed
    derivative_failed = 0
    indexed = already_ingested
    skipped_count = already_skipped
    processed_bytes = already_bytes

    for offset_idx, candidate_id in enumerate(candidate_ids, 1):
        if _shutdown_requested.is_set():
            log.info("Stopping ingestion loop cleanly on shutdown signal; job %s remains resumable.", job_id)
            return
        index = already_ingested + offset_idx
        current = _job(job_id)
        if not current or current.cancel_requested:
            _finish_cancelled(job_id)
            return
        stored_path = None
        created_frames: list[Path] = []
        created_derivatives: list[Path] = []
        item_derivative_failures = 0
        with SessionLocal() as db:
            candidate = db.get(JobCandidate, candidate_id)
            job = db.get(IngestionJob, job_id)
            source_id = job.source_id if job else ""
            source = db.get(SourceDefinition, source_id) if source_id else None
            try:
                if db.bind is not None and db.bind.dialect.name == "postgresql" and candidate.sha256:
                    digest_lock = int(candidate.sha256[:16], 16)
                    if digest_lock >= 2**63:
                        digest_lock -= 2**64
                    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": digest_lock})

                root = _resolve_source(db, source_id)
                path = _candidate_path(root, candidate.source_relative_key)
                st = path.stat()
                mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))
                device_inode = f"{getattr(st, 'st_dev', 0)}:{getattr(st, 'st_ino', 0)}"

                cand_meta = candidate.metadata_json or {}
                inc_action = cand_meta.get("incremental_action", "new")
                context_data = cand_meta.get("context") or infer_path_context(candidate.source_relative_key)

                existing_asset = db.scalar(select(MediaAsset).where(MediaAsset.sha256 == candidate.sha256))

                # Handle MOVED / RENAMED file without re-embedding visual/audio/document vectors!
                if existing_asset is not None and inc_action == "moved":
                    old_rel = existing_asset.source_relative_key
                    existing_asset.source_relative_key = candidate.source_relative_key
                    existing_asset.original_filename = candidate.original_filename
                    source_paths = list({*(existing_asset.metadata_json or {}).get("source_paths", []), candidate.source_relative_key})
                    existing_asset.metadata_json = (existing_asset.metadata_json or {}) | {
                        "context": context_data,
                        "moved_from": old_rel,
                        "source_paths": source_paths,
                    }
                    # Refresh contextual_metadata segment only
                    db.execute(
                        delete(SearchSegment).where(
                            SearchSegment.asset_id == existing_asset.id,
                            SearchSegment.segment_type == "contextual_metadata",
                        )
                    )
                    if context_data.get("context_text"):
                        _store_text_embeddings(db, existing_asset, context_data["context_text"], "context", segment_type="contextual_metadata")
                    if old_rel:
                        old_rec = db.scalar(
                            select(SourceFileRecord).where(
                                SourceFileRecord.source_id == source_id,
                                SourceFileRecord.source_relative_key == old_rel,
                            )
                        )
                        if old_rec:
                            old_rec.status = "moved"
                    _upsert_source_file_record(
                        db,
                        source_id=source_id,
                        relative_key=candidate.source_relative_key,
                        asset=existing_asset,
                        size_bytes=int(st.st_size),
                        mtime_ns=mtime_ns,
                        device_inode=device_inode,
                        job_id=job_id,
                    )
                    candidate.state = "ingested"
                    candidate.message = f"Moved from {old_rel}; updated path and context without re-embedding."
                    processed_bytes += int(st.st_size)
                    job.progress_done = index
                    job.processed_files = index
                    job.processed_bytes = processed_bytes
                    job.heartbeat_at = datetime.now(timezone.utc)
                    db.commit()
                    succeeded += 1
                    indexed += 1
                    continue

                # Handle DUPLICATE content while recording additional source_relative_key relationship
                if existing_asset is not None and inc_action != "modified":
                    source_paths = list({
                        *(existing_asset.metadata_json or {}).get("source_paths", []),
                        *([existing_asset.source_relative_key] if existing_asset.source_relative_key else []),
                        candidate.source_relative_key,
                    })
                    existing_asset.metadata_json = (existing_asset.metadata_json or {}) | {"source_paths": source_paths}
                    _upsert_source_file_record(
                        db,
                        source_id=source_id,
                        relative_key=candidate.source_relative_key,
                        asset=existing_asset,
                        size_bytes=int(st.st_size),
                        mtime_ns=mtime_ns,
                        device_inode=device_inode,
                        job_id=job_id,
                    )
                    candidate.state = "duplicate"
                    candidate.message = "Matching content already catalogued; source path linked."
                    processed_bytes += int(st.st_size)
                    skipped_count += 1
                    job.progress_done = index
                    job.processed_files = index
                    job.processed_bytes = processed_bytes
                    job.skipped_files = skipped_count
                    job.heartbeat_at = datetime.now(timezone.utc)
                    db.commit()
                    continue

                metadata = inspect_media(path)
                if sha256_file(path) != candidate.sha256:
                    raise MediaValidationError("source changed after preview")

                is_browser_import = bool(source and source.name == "Browser file import")
                suffix = path.suffix.lower()
                storage_key = f"{candidate.sha256[:2]}/{candidate.sha256}{suffix}"

                if is_browser_import or settings.copy_source_originals:
                    stored_path = copy_original(path, storage_key)
                    if sha256_file(stored_path) != candidate.sha256:
                        raise MediaValidationError("source changed while it was being copied")
                    working_media_path = stored_path
                else:
                    # Read-only HDD reference mode: keep originals untouched on approved drive
                    working_media_path = path

                # Handle MODIFIED existing asset at same source_relative_key
                existing_at_path = db.scalar(
                    select(MediaAsset).where(
                        MediaAsset.source_id == source.id,
                        MediaAsset.source_relative_key == candidate.source_relative_key,
                    )
                ) if source else None

                if existing_at_path is not None:
                    asset = existing_at_path
                    asset.sha256 = candidate.sha256
                    asset.storage_key = storage_key
                    asset.size_bytes = metadata["size"]
                    asset.detected_mime = metadata["mime"]
                    asset.width = metadata.get("width")
                    asset.height = metadata.get("height")
                    asset.duration_ms = metadata.get("duration_ms")
                    asset.metadata_json = (asset.metadata_json or {}) | {
                        "ingestion_job_id": job_id,
                        "context": context_data,
                        "source_paths": [candidate.source_relative_key],
                    }
                    db.execute(delete(SearchSegment).where(SearchSegment.asset_id == asset.id))
                    db.execute(delete(AssetEmbedding).where(AssetEmbedding.asset_id == asset.id))
                    db.execute(delete(DetectedArchiveFace).where(DetectedArchiveFace.asset_id == asset.id))
                    db.execute(delete(VideoFrame).where(VideoFrame.asset_id == asset.id))
                else:
                    asset_id = str(uuid.uuid4())
                    asset = MediaAsset(
                        id=asset_id,
                        source_id=source.id if source else None,
                        source_relative_key=candidate.source_relative_key,
                        original_filename=candidate.original_filename,
                        media_type=metadata["media_type"],
                        detected_mime=metadata["mime"],
                        size_bytes=metadata["size"],
                        sha256=candidate.sha256,
                        storage_key=storage_key,
                        width=metadata.get("width"),
                        height=metadata.get("height"),
                        duration_ms=metadata.get("duration_ms"),
                        metadata_json={
                            "ingestion_job_id": job_id,
                            "context": context_data,
                            "source_paths": [candidate.source_relative_key],
                            "stored_in_archive": bool(is_browser_import or settings.copy_source_originals),
                        },
                    )
                    db.add(asset)

                db.flush()

                # 1. Contextual metadata segment
                context_text = (context_data or {}).get("context_text", "")
                if context_text:
                    _store_text_embeddings(db, asset, context_text, "context", segment_type="contextual_metadata")

                # 2. Modality-specific multimodal extraction & segment indexing
                if asset.media_type == "image":
                    job.current_stage = "embedding_image"
                    try:
                        thumbnail_key = f"{candidate.sha256[:2]}/{candidate.sha256}.thumb.webp"
                        created_derivatives.append(image_thumbnail(working_media_path, thumbnail_key))
                        asset.thumbnail_key = thumbnail_key
                    except MediaValidationError:
                        asset.metadata_json = asset.metadata_json | {"derivative_status": "thumbnail_unavailable"}
                        item_derivative_failures += 1

                    emb_target = created_derivatives[-1] if created_derivatives else working_media_path
                    vector, model_id, model_revision = image_embedding(emb_target)
                    _add_segment_and_embedding(
                        db,
                        asset,
                        segment_type="image_visual",
                        modality="image",
                        ordinal=1,
                        title=asset.original_filename,
                        vector=vector,
                        model_id=model_id,
                        model_revision=model_revision,
                        metadata_json={"is_visual_claim": True},
                    )

                    # Visual description & object/keyword suggestions
                    vis_desc = generate_visual_description(vector)
                    asset.metadata_json = asset.metadata_json | {"visual_analysis": vis_desc}
                    if vis_desc.get("description"):
                        _store_text_embeddings(
                            db,
                            asset,
                            vis_desc["description"],
                            "visual_description",
                            segment_type="visual_description",
                        )

                    # Image OCR
                    job.current_stage = "extracting_ocr"
                    try:
                        ocr_text = extract_image_text(working_media_path)
                        asset.metadata_json = asset.metadata_json | {
                            "text_extraction": {"status": "ready" if ocr_text else "no_text", "text": ocr_text or ""}
                        }
                        if ocr_text:
                            _store_text_embeddings(db, asset, ocr_text, "ocr", segment_type="ocr_text")
                    except MediaValidationError:
                        asset.metadata_json = asset.metadata_json | {"text_extraction": {"status": "unavailable", "text": ""}}

                    # Dedicated InsightFace buffalo_l face recognition (when authorized)
                    if is_biometric_governance_authorized():
                        job.current_stage = "detecting_faces"
                        try:
                            face_res = process_archive_asset_faces(db, asset, working_media_path)
                            asset.metadata_json = asset.metadata_json | {
                                "face_recognition": {"status": "ready", "model_pack": settings.face_model_pack, **face_res}
                            }
                        except Exception as face_err:
                            log.warning("InsightFace image processing skipped on %s: %s", asset.id, face_err)
                            asset.metadata_json = asset.metadata_json | {
                                "face_recognition": {"status": "unavailable", "error": str(face_err)}
                            }

                elif asset.media_type == "video":
                    job.current_stage = "sampling_video_frames"
                    frame_failures, embedding_sources = _store_video_frames(db, asset, working_media_path, metadata, created_frames)
                    item_derivative_failures += frame_failures
                    db.flush()
                    if not embedding_sources:
                        raise MediaValidationError("No image frame was available for semantic indexing")
                    interval_ms = settings.video_frame_interval_seconds * 1000
                    video_faces_detected = 0
                    video_candidate_matches = 0
                    for frame_ord, (frame, image_path) in enumerate(embedding_sources, start=1):
                        vector, model_id, model_revision = image_embedding(image_path)
                        _add_segment_and_embedding(
                            db,
                            asset,
                            segment_type="video_frame",
                            modality="image_frame",
                            ordinal=frame_ord,
                            title=f"{asset.original_filename} @ {frame.timestamp_ms}ms",
                            vector=vector,
                            model_id=model_id,
                            model_revision=model_revision,
                            frame_id=frame.id,
                            start_ms=frame.timestamp_ms,
                            end_ms=frame.timestamp_ms + interval_ms,
                            metadata_json={"is_visual_claim": True},
                        )
                        if is_biometric_governance_authorized():
                            try:
                                f_res = process_archive_asset_faces(db, asset, image_path, frame=frame)
                                video_faces_detected += int(f_res.get("faces_detected", 0))
                                video_candidate_matches += int(f_res.get("candidate_matches", 0))
                            except Exception as face_err:
                                log.warning("InsightFace video frame processing skipped on %s: %s", frame.id, face_err)
                    if is_biometric_governance_authorized():
                        asset.metadata_json = asset.metadata_json | {
                            "face_recognition": {
                                "status": "ready",
                                "model_pack": settings.face_model_pack,
                                "faces_detected": video_faces_detected,
                                "candidate_matches": video_candidate_matches,
                            }
                        }

                    job.current_stage = "transcribing_video_audio"
                    try:
                        heartbeat_stop, heartbeat_thread = _start_heartbeat(job_id, "transcribing_video_audio")
                        try:
                            transcript = transcribe_audio(extract_audio(working_media_path, metadata["duration_ms"] or 0))
                        finally:
                            heartbeat_stop.set()
                            heartbeat_thread.join()
                        windows = build_transcript_windows(
                            transcript,
                            duration_ms=metadata.get("duration_ms"),
                            window_ms=settings.audio_window_seconds * 1000,
                            hop_ms=settings.audio_window_hop_seconds * 1000,
                            max_chars=settings.document_chunk_chars,
                        )
                        asset.metadata_json = asset.metadata_json | {
                            "transcription": {
                                "status": "ready",
                                "language": transcript["language"],
                                "text": transcript["text"],
                                "english_translation": transcript["english_translation"],
                                "model_id": transcript["model_id"],
                                "model_revision": transcript["model_revision"],
                                "window_count": len(windows),
                            }
                        }
                        for win in windows:
                            _store_text_embeddings(
                                db,
                                asset,
                                win["text"],
                                "transcript",
                                segment_type="audio_transcript_window",
                                start_ms=win["start_ms"],
                                end_ms=win["end_ms"],
                                base_ordinal=win["ordinal"],
                            )
                        if transcript.get("english_translation") and transcript["english_translation"] != transcript.get("text"):
                            _store_text_embeddings(
                                db,
                                asset,
                                transcript["english_translation"],
                                "translation",
                                segment_type="audio_transcript_window",
                                start_ms=0,
                                end_ms=metadata.get("duration_ms") or 0,
                            )
                    except MediaValidationError:
                        asset.metadata_json = asset.metadata_json | {"transcription": {"status": "unavailable"}}

                elif asset.media_type == "audio":
                    job.current_stage = "transcribing_audio"
                    try:
                        heartbeat_stop, heartbeat_thread = _start_heartbeat(job_id, "transcribing_audio")
                        try:
                            audio_bytes = extract_audio(working_media_path, metadata["duration_ms"] or 0)
                            transcript = transcribe_audio(audio_bytes)
                        finally:
                            heartbeat_stop.set()
                            heartbeat_thread.join()
                        windows = build_transcript_windows(
                            transcript,
                            duration_ms=metadata.get("duration_ms"),
                            window_ms=settings.audio_window_seconds * 1000,
                            hop_ms=settings.audio_window_hop_seconds * 1000,
                            max_chars=settings.document_chunk_chars,
                        )
                        asset.metadata_json = asset.metadata_json | {
                            "transcription": {
                                "status": "ready",
                                "language": transcript["language"],
                                "text": transcript["text"],
                                "english_translation": transcript["english_translation"],
                                "model_id": transcript["model_id"],
                                "model_revision": transcript["model_revision"],
                                "window_count": len(windows),
                            }
                        }
                        for win in windows:
                            _store_text_embeddings(
                                db,
                                asset,
                                win["text"],
                                "audio_transcript",
                                segment_type="audio_transcript_window",
                                start_ms=win["start_ms"],
                                end_ms=win["end_ms"],
                                base_ordinal=win["ordinal"],
                            )
                        if transcript.get("english_translation") and transcript["english_translation"] != transcript.get("text"):
                            _store_text_embeddings(
                                db,
                                asset,
                                transcript["english_translation"],
                                "translation",
                                segment_type="audio_transcript_window",
                                start_ms=0,
                                end_ms=metadata.get("duration_ms") or 0,
                            )
                    except MediaValidationError:
                        asset.metadata_json = asset.metadata_json | {"transcription": {"status": "unavailable"}}

                elif asset.media_type == "document" or path.suffix.lower() in SUPPORTED_DOCUMENT_EXTENSIONS:
                    job.current_stage = "extracting_document"
                    try:
                        extracted = extract_document_text(
                            working_media_path,
                            chunk_chars=settings.document_chunk_chars,
                            chunk_overlap=settings.document_chunk_overlap,
                        )
                        segments = extracted.get("segments", [])
                        for seg_item in segments:
                            text_value = (seg_item.get("text") or "").strip()
                            if text_value:
                                kind = seg_item.get("kind", "text")
                                seg_type = seg_item.get("segment_type", "office_chunk")
                                _store_text_embeddings(
                                    db,
                                    asset,
                                    text_value,
                                    f"document_{kind}",
                                    segment_type=seg_type,
                                    page_number=seg_item.get("page_number") or seg_item.get("ordinal"),
                                    title=seg_item.get("title") or asset.original_filename,
                                    base_ordinal=int(seg_item.get("ordinal") or 1),
                                )
                        asset.metadata_json = asset.metadata_json | {
                            "text_extraction": {
                                "status": "ready" if extracted.get("text") else "no_text",
                                "text": extracted.get("text", "")[:500000],
                                "page_count": extracted.get("page_count", len(segments)),
                                "segments": [
                                    {
                                        "kind": item.get("kind"),
                                        "segment_type": item.get("segment_type"),
                                        "ordinal": item.get("ordinal"),
                                        "page_number": item.get("page_number"),
                                        "title": item.get("title"),
                                    }
                                    for item in segments
                                ],
                            }
                        }
                    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
                        asset.metadata_json = asset.metadata_json | {"text_extraction": {"status": "unavailable", "text": ""}}

                _upsert_source_file_record(
                    db,
                    source_id=source.id if source else None,
                    relative_key=candidate.source_relative_key,
                    asset=asset,
                    size_bytes=metadata["size"],
                    mtime_ns=mtime_ns,
                    device_inode=device_inode,
                    job_id=job_id,
                )

                indexed += 1
                candidate.state = "ingested"
                processed_bytes += int(metadata["size"])
                job.progress_done = index
                job.processed_files = index
                job.processed_bytes = processed_bytes
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
                        item.message = "Storage free-space stop threshold reached." if storage_stop else f"File could not be processed safely ({error})."
                    if job:
                        job.progress_done = index
                        job.processed_files = index
                        job.failed_files = int(job.failed_files or 0) + (0 if storage_stop else 1)
                        job.heartbeat_at = None if storage_stop else datetime.now(timezone.utc)
                        if storage_stop:
                            job.state = JobState.failed.value
                            job.current_stage = "stopped_storage_threshold"
                            job.completed_at = datetime.now(timezone.utc)
                            job.error_summary = "Storage free-space stop threshold reached."
                            job.summary = {
                                "ingested": succeeded,
                                "failed": failed + 1,
                                "derivative_failures": derivative_failed,
                                "skipped": max(0, job.progress_total - index),
                            }
                            failed_db.add(AuditEvent(actor_id=job.requested_by, action="ingestion_stopped_storage_threshold", target_type="job", target_id=job.id, detail=job.summary))
                failed += 1
                log.warning(
                    "A media item failed during ingestion job %s (%s: %s).",
                    job_id,
                    type(error).__name__,
                    error,
                )
                if storage_stop:
                    return

    with SessionLocal.begin() as db:
        job = db.get(IngestionJob, job_id)
        if not job:
            return
        job.heartbeat_at = None
        job.completed_at = datetime.now(timezone.utc)
        job.current_stage = "cancelled" if job.cancel_requested else "completed"
        telemetry = compute_job_telemetry(job)
        job.summary = {
            "ingested": succeeded,
            "indexed": indexed,
            "failed": failed,
            "derivative_failures": derivative_failed,
            "skipped": skipped_count,
            "processed_bytes": processed_bytes,
            "elapsed_seconds": telemetry["elapsed_seconds"],
            "files_per_second": telemetry["files_per_second"],
            "mb_per_second": telemetry["mb_per_second"],
        }
        job.state = (
            JobState.cancelled.value
            if job.cancel_requested
            else JobState.partially_completed.value
            if failed or derivative_failed
            else JobState.completed.value
        )
        db.add(AuditEvent(actor_id=job.requested_by, action="ingestion_completed", target_type="job", target_id=job.id, detail=job.summary))
        source = db.get(SourceDefinition, job.source_id)
        if source and source.name == "Browser file import" and not failed:
            shutil.rmtree(source.root_path, ignore_errors=True)


def _finish_cancelled(job_id):
    with SessionLocal.begin() as db:
        job = db.get(IngestionJob, job_id)
        if job:
            job.state = JobState.cancelled.value
            job.current_stage = "cancelled"
            job.completed_at = datetime.now(timezone.utc)
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
                    row.current_stage = "failed"
                    row.error_summary = "Unsupported job type."
    return True


def main():
    _install_signal_handlers()
    while not _shutdown_requested.is_set():
        try:
            if not run_once():
                time.sleep(1)
        except Exception:
            log.exception("Worker cycle failed.")
            time.sleep(2)


if __name__ == "__main__":
    main()
