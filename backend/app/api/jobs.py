import hashlib
import json
import re
import shutil
import sys
import uuid
from hashlib import sha256
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.config import settings
from app.database import get_db
from app.deps import csrf_check, roles
from app.models import AuditEvent, IngestionJob, JobCandidate, JobState, MediaAsset, SourceDefinition, User
from app.services.contextual_metadata import infer_path_context
from app.services.crawler import ALL_SUPPORTED_EXTENSIONS
from app.services.ingest_explorer import browse_drive_from_explorer, get_system_drives, register_drive_source, sync_active_drive_env
from app.services.media import MediaValidationError, inspect_media
from app.worker import compute_job_telemetry

router = APIRouter(prefix="/api", tags=["ingestion and jobs"])


def configured_sources(db: Session | None = None):
    roots = []
    if settings.demo_mode:
        roots.append((settings.demo_media_root, True))
    else:
        roots.extend((path, False) for path in settings.approved_source_roots)
    sources = []
    for root, demo in roots:
        path = root.resolve()
        if not path.is_dir():
            continue
        source_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"iihs-source:{path}"))
        sources.append({"id": source_id, "name": path.name or "Approved source", "demo": demo, "enabled": True})

    if db is not None:
        try:
            db_sources = db.scalars(select(SourceDefinition).where(SourceDefinition.enabled == True)).all()
            for s in db_sources:
                if not any(item["id"] == s.id for item in sources):
                    sources.append({"id": s.id, "name": s.name, "demo": s.is_demo, "enabled": s.enabled})
        except Exception:
            pass

    try:
        for drive in get_system_drives():
            if not drive.get("ready"):
                continue
            drive_root = drive["drive"]
            drive_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"iihs-source:{drive_root}"))
            if not any(item["id"] == drive_id for item in sources):
                label = drive.get("label") or "Drive"
                total_gb = drive.get("total_gb", 0)
                name = f"{drive_root} {label} ({total_gb} GB)"
                sources.append({"id": drive_id, "name": name, "demo": False, "enabled": True, "drive_path": drive_root})
    except Exception:
        pass

    return sources


@router.get("/sources")
def list_sources(
    user: Annotated[User, Depends(roles("admin", "archivist"))],
    db: Annotated[Session, Depends(get_db)],
):
    return configured_sources(db=db)


@router.get("/sources/drives")
def list_system_drives_endpoint(user: Annotated[User, Depends(roles("admin", "archivist"))]):
    return {"drives": get_system_drives()}


class BrowseExplorerRequest(BaseModel):
    title: str = "Select Drive or Folder for IIHS Media Archive Ingestion"


@router.post("/sources/browse-explorer")
def browse_explorer_endpoint(
    request: Request,
    body: BrowseExplorerRequest,
    actor: Annotated[User, Depends(roles("admin", "archivist"))],
    db: Annotated[Session, Depends(get_db)],
    csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
    csrf_cookie: str | None = Cookie(default=None, alias="iihs_csrf"),
):
    csrf_check(request, csrf_header, csrf_cookie)
    res = browse_drive_from_explorer(title=body.title)
    if res.get("cancelled") or not res.get("path"):
        return {"cancelled": True, "message": "Explorer selection was cancelled."}
    selected_path = res["path"]
    name = res.get("name") or f"Drive ({selected_path})"
    source = register_drive_source(db=db, drive_path=selected_path, name=name, user_id=actor.id)
    return {
        "cancelled": False,
        "source": {
            "id": source.id,
            "name": source.name,
            "demo": source.is_demo,
            "enabled": source.enabled,
        },
        "path": selected_path,
        "drive_letter": res.get("drive_letter"),
        "drive_info": res.get("drive_info"),
    }


class RegisterDriveRequest(BaseModel):
    path: str
    name: str | None = None


@router.post("/sources/register-drive")
def register_drive_endpoint(
    request: Request,
    body: RegisterDriveRequest,
    actor: Annotated[User, Depends(roles("admin", "archivist"))],
    db: Annotated[Session, Depends(get_db)],
    csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
    csrf_cookie: str | None = Cookie(default=None, alias="iihs_csrf"),
):
    csrf_check(request, csrf_header, csrf_cookie)
    source = register_drive_source(db=db, drive_path=body.path, name=body.name, user_id=actor.id)
    return {
        "source": {
            "id": source.id,
            "name": source.name,
            "demo": source.is_demo,
            "enabled": source.enabled,
        }
    }


class PreviewRequest(BaseModel):
    source_id: str
    max_files: int = Field(default=100, ge=1)


def _job_json(job: IngestionJob):
    telemetry = compute_job_telemetry(job)
    return {
        "id": job.id,
        "kind": job.kind,
        "state": job.state,
        "source_id": job.source_id,
        "progress": {
            "done": job.progress_done,
            "total": job.progress_total,
            **telemetry,
        },
        **telemetry,
        "summary": job.summary,
        "error": job.error_summary,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


@router.post("/ingestions/previews", status_code=202)
def create_preview(
    body: PreviewRequest,
    request: Request,
    actor: Annotated[User, Depends(roles("admin", "archivist"))],
    db: Annotated[Session, Depends(get_db)],
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=16, max_length=64),
    csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
    csrf_cookie: str | None = Cookie(default=None, alias="iihs_csrf"),
):
    csrf_check(request, csrf_header, csrf_cookie)
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", idempotency_key):
        raise HTTPException(status_code=400, detail="Invalid idempotency key.")
    signature = hashlib.sha256(json.dumps(body.model_dump(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    existing = db.scalar(select(IngestionJob).where(IngestionJob.requested_by == actor.id, IngestionJob.idempotency_key == idempotency_key))
    if existing:
        if existing.request_signature != signature:
            raise HTTPException(status_code=409, detail="Idempotency key was already used for a different request.")
        return _job_json(existing)
    if settings.max_files_per_job and body.max_files > settings.max_files_per_job:
        raise HTTPException(status_code=422, detail=f"max_files cannot exceed the configured limit of {settings.max_files_per_job}.")
    source = next((item for item in configured_sources(db=db) if item["id"] == body.source_id), None)
    if source is None:
        raise HTTPException(status_code=404, detail="Approved source is unavailable.")
    roots = [settings.demo_media_root] if source["demo"] else list(settings.approved_source_roots)
    root = next((path.resolve() for path in roots if str(uuid.uuid5(uuid.NAMESPACE_URL, f"iihs-source:{path.resolve()}")) == source["id"]), None)
    db_source = db.get(SourceDefinition, source["id"])
    if root is None and db_source and Path(db_source.root_path).is_dir():
        root = Path(db_source.root_path)
    if root is None:
        ext_hdd = Path("/mnt/approved_sources/IIHS_External_HDD")
        if ext_hdd.is_dir():
            root = ext_hdd
        elif source.get("drive_path") and Path(source["drive_path"]).is_dir():
            root = Path(source["drive_path"])
    if root is None or not root.is_dir():
        raise HTTPException(status_code=404, detail="Approved source is unavailable.")
    if db_source is None:
        db_source = SourceDefinition(id=source["id"], name=source["name"], root_path=str(root), is_demo=source["demo"], enabled=True)
        db.add(db_source)
        db.flush()
    else:
        db_source.root_path = str(root)
        db_source.enabled = True
        db.flush()
    job = IngestionJob(
        kind="preview",
        state=JobState.queued.value,
        source_id=db_source.id,
        requested_by=actor.id,
        max_files=body.max_files,
        idempotency_key=idempotency_key,
        request_signature=signature,
        current_stage="queued",
    )
    db.add(job)
    db.flush()
    db.add(
        AuditEvent(
            actor_id=actor.id,
            action="ingestion_preview_created",
            target_type="job",
            target_id=job.id,
            request_id=getattr(request.state, "request_id", None),
            detail={"source_id": db_source.id, "max_files": body.max_files},
        )
    )
    db.commit()
    return _job_json(job)


@router.post("/ingestions/imports", status_code=202)
async def import_files(
    request: Request,
    actor: Annotated[User, Depends(roles("admin", "archivist"))],
    db: Annotated[Session, Depends(get_db)],
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=16, max_length=64),
    csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
    csrf_cookie: str | None = Cookie(default=None, alias="iihs_csrf"),
):
    csrf_check(request, csrf_header, csrf_cookie)
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", idempotency_key):
        raise HTTPException(status_code=400, detail="Invalid idempotency key.")
    form = None
    import_root = None
    try:
        form = await request.form(max_files=sys.maxsize, max_fields=sys.maxsize)
        files = [item for item in form.getlist("files") if isinstance(item, UploadFile)]
        if not files:
            raise HTTPException(status_code=422, detail="Select at least one file.")
        if settings.max_files_per_job and len(files) > settings.max_files_per_job:
            raise HTTPException(status_code=422, detail=f"Select between 1 and {settings.max_files_per_job} files.")
        import_root = settings.storage_root / "imports" / str(uuid.uuid4())
        import_root.mkdir(parents=True, mode=0o700)
        prepared = []
        total_bytes = 0
        seen_hashes = set()
        allowed = ALL_SUPPORTED_EXTENSIONS
        for index, upload in enumerate(files, 1):
            raw_upload_name = "".join(character for character in (upload.filename or "").replace("\\", "/").strip() if character.isprintable())
            filename = Path(raw_upload_name).name[:255]
            suffix = Path(filename).suffix.lower()
            key = f"{index:05d}{suffix}"
            path = import_root / key
            digest = sha256()
            size = 0
            message = None
            metadata = {}
            state = "ready"
            if not filename or suffix not in allowed:
                state, message = "invalid", "Unsupported file type."
            else:
                try:
                    with path.open("xb") as destination:
                        while chunk := await upload.read(1024 * 1024):
                            size += len(chunk)
                            total_bytes += len(chunk)
                            if (settings.max_file_bytes and size > settings.max_file_bytes) or (settings.max_import_bytes and total_bytes > settings.max_import_bytes):
                                raise HTTPException(status_code=413, detail="The selected files exceed the configured import size limit.")
                            if shutil.disk_usage(settings.storage_root).free - len(chunk) < settings.min_storage_free_bytes:
                                raise HTTPException(status_code=503, detail="Storage is below the import safety threshold.")
                            digest.update(chunk)
                            destination.write(chunk)
                    metadata = inspect_media(path)
                    metadata["context"] = infer_path_context(raw_upload_name or filename)
                    if (
                        digest.hexdigest() in seen_hashes
                        or db.scalar(select(MediaAsset.id).where(MediaAsset.sha256 == digest.hexdigest()))
                        or db.scalar(
                            select(JobCandidate.id)
                            .join(IngestionJob)
                            .where(
                                JobCandidate.sha256 == digest.hexdigest(),
                                JobCandidate.state.in_(["ready", "ingested"]),
                                IngestionJob.kind == "ingest",
                            )
                        )
                    ):
                        state, message = "duplicate", "Already catalogued or duplicated in this import."
                    seen_hashes.add(digest.hexdigest())
                    if state != "ready":
                        path.unlink(missing_ok=True)
                except HTTPException:
                    raise
                except MediaValidationError as exc:
                    state, message = "invalid", f"File failed media validation ({exc})."
                    path.unlink(missing_ok=True)
                except OSError:
                    state, message = "invalid", "File failed media validation."
                    path.unlink(missing_ok=True)
            prepared.append((key, filename or "unnamed", size, digest.hexdigest() if size else "", state, message, metadata))
        signature = sha256(json.dumps([(item[1], item[3], item[4]) for item in prepared], separators=(",", ":")).encode()).hexdigest()
        existing = db.scalar(select(IngestionJob).where(IngestionJob.requested_by == actor.id, IngestionJob.idempotency_key == idempotency_key))
        if existing:
            shutil.rmtree(import_root, ignore_errors=True)
            if existing.request_signature != signature:
                raise HTTPException(status_code=409, detail="Idempotency key was already used for a different import.")
            return _job_json(existing)
        source_id = str(uuid.uuid4())
        source = SourceDefinition(id=source_id, name="Browser file import", root_path=str(import_root.resolve()), is_demo=settings.demo_mode, enabled=True)
        db.add(source)
        db.flush()
        skipped_cnt = sum(1 for item in prepared if item[4] == "duplicate")
        failed_cnt = sum(1 for item in prepared if item[4] == "invalid")
        job = IngestionJob(
            kind="ingest",
            state=JobState.queued.value,
            source_id=source_id,
            requested_by=actor.id,
            max_files=len(files),
            idempotency_key=idempotency_key,
            request_signature=signature,
            progress_total=len(files),
            discovered_files=len(files),
            total_bytes=total_bytes,
            skipped_files=skipped_cnt,
            failed_files=failed_cnt,
            current_stage="queued",
        )
        db.add(job)
        db.flush()
        for key, filename, size, checksum, state, message, metadata in prepared:
            db.add(
                JobCandidate(
                    job_id=job.id,
                    source_relative_key=key,
                    original_filename=filename,
                    media_type=metadata.get("media_type", "unknown"),
                    size_bytes=size,
                    sha256=checksum,
                    state=state,
                    message=message,
                    metadata_json=metadata,
                )
            )
        db.add(
            AuditEvent(
                actor_id=actor.id,
                action="files_imported",
                target_type="job",
                target_id=job.id,
                request_id=getattr(request.state, "request_id", None),
                detail={"file_count": len(files), "upload_bytes": total_bytes},
            )
        )
        db.commit()
        return _job_json(job)
    except Exception:
        if import_root is not None:
            shutil.rmtree(import_root, ignore_errors=True)
        raise
    finally:
        if form is not None:
            await form.close()


@router.get("/jobs")
def list_jobs(
    user: Annotated[User, Depends(roles("admin", "archivist", "operator"))],
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(default=50, ge=1, le=100),
):
    return [_job_json(job) for job in db.scalars(select(IngestionJob).order_by(IngestionJob.created_at.desc()).limit(limit)).all()]


@router.get("/jobs/{job_id}")
def get_job(
    job_id: str,
    user: Annotated[User, Depends(roles("admin", "archivist", "operator"))],
    db: Annotated[Session, Depends(get_db)],
):
    job = db.get(IngestionJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    result = _job_json(job)
    if job.state == JobState.preview_ready.value:
        result["preview_files"] = [
            {"path": row.source_relative_key, "type": row.media_type, "size_bytes": row.size_bytes, "state": row.state, "message": row.message}
            for row in db.scalars(select(JobCandidate).where(JobCandidate.job_id == job.id).order_by(JobCandidate.source_relative_key)).all()
        ]
    elif (source := db.get(SourceDefinition, job.source_id)) and source.name == "Browser file import":
        result["preview_files"] = [
            {"path": row.original_filename, "type": row.media_type, "size_bytes": row.size_bytes, "state": row.state, "message": row.message}
            for row in db.scalars(select(JobCandidate).where(JobCandidate.job_id == job.id).order_by(JobCandidate.source_relative_key)).all()
        ]
    return result


@router.post("/jobs/{job_id}/confirm")
def confirm_preview(
    job_id: str,
    request: Request,
    actor: Annotated[User, Depends(roles("admin", "archivist"))],
    db: Annotated[Session, Depends(get_db)],
    csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
    csrf_cookie: str | None = Cookie(default=None, alias="iihs_csrf"),
):
    csrf_check(request, csrf_header, csrf_cookie)
    job = db.get(IngestionJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.kind != "preview" or job.state != JobState.preview_ready.value:
        raise HTTPException(status_code=409, detail="Only a completed preview can be confirmed.")
    job.kind = "ingest"
    job.state = JobState.queued.value
    job.current_stage = "queued"
    job.started_at = None
    job.completed_at = None
    job.progress_done = 0
    job.processed_files = 0
    job.processed_bytes = 0
    job.requested_by = actor.id
    db.add(
        AuditEvent(
            actor_id=actor.id,
            action="ingestion_confirmed",
            target_type="job",
            target_id=job.id,
            request_id=getattr(request.state, "request_id", None),
            detail={"source_id": job.source_id, "candidate_count": job.progress_total},
        )
    )
    db.commit()
    return _job_json(job)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(
    job_id: str,
    request: Request,
    actor: Annotated[User, Depends(roles("admin", "archivist"))],
    db: Annotated[Session, Depends(get_db)],
    csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
    csrf_cookie: str | None = Cookie(default=None, alias="iihs_csrf"),
):
    csrf_check(request, csrf_header, csrf_cookie)
    job = db.get(IngestionJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.state == JobState.queued.value:
        job.state = JobState.cancelled.value
        job.current_stage = "cancelled"
    elif job.state == JobState.running.value:
        job.cancel_requested = True
        job.state = JobState.cancel_requested.value
        job.current_stage = "cancel_requested"
    else:
        raise HTTPException(status_code=409, detail="This job cannot be cancelled in its current state.")
    db.add(AuditEvent(actor_id=actor.id, action="job_cancel_requested", target_type="job", target_id=job.id, request_id=getattr(request.state, "request_id", None)))
    db.commit()
    return _job_json(job)


@router.post("/jobs/{job_id}/retry")
def retry_job(
    job_id: str,
    request: Request,
    actor: Annotated[User, Depends(roles("admin"))],
    db: Annotated[Session, Depends(get_db)],
    csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
    csrf_cookie: str | None = Cookie(default=None, alias="iihs_csrf"),
):
    csrf_check(request, csrf_header, csrf_cookie)
    job = db.get(IngestionJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.state not in {JobState.failed.value, JobState.partially_completed.value}:
        raise HTTPException(status_code=409, detail="Only failed jobs can be retried.")
    job.state = JobState.queued.value
    job.current_stage = "queued"
    job.completed_at = None
    job.cancel_requested = False
    job.error_summary = None
    job.failed_files = 0
    for item in job.candidates:
        if item.state == "failed":
            item.state = "ready"
            item.message = None
    db.add(AuditEvent(actor_id=actor.id, action="job_retried", target_type="job", target_id=job.id, request_id=getattr(request.state, "request_id", None)))
    db.commit()
    return _job_json(job)
