from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, or_
from sqlalchemy.orm import Session
from .config import settings
from .db import Base, engine, get_db
from .models import Asset, Tag, IngestJob, EmployeeProfile
from .schemas import IngestPreviewRequest, TagCreate, EmployeeProfileCreate, SearchRequest
from .services.paths import approved_roots, resolve_approved_path
from .services.jobs import create_job
from .services.media import SUPPORTED_EXTENSIONS, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, sha256_file, file_type, image_metadata, create_thumbnail, video_metadata, extract_pdf_text
from .services.embeddings import embedding_service
import os

app = FastAPI(title=settings.app_name, version="0.1.0", description="Local-first media archive API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup() -> None:
    for folder in (settings.storage_root, settings.derivatives_root, settings.people_root):
        folder.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.app_name, "embedding_model_loaded": embedding_service.model is not None}


@app.get(settings.api_prefix + "/sources")
def list_sources():
    try:
        roots = approved_roots()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return [{"id": str(i), "name": p.name or str(p), "path": str(p)} for i, p in enumerate(roots)]


@app.post(settings.api_prefix + "/ingest/preview")
def preview_ingest(payload: IngestPreviewRequest):
    if payload.source_kind == "upload_batch":
        raise HTTPException(status_code=400, detail="Use the upload endpoint for browser-selected files")
    if payload.source_kind == "people_directory":
        root = settings.people_root.resolve()
        candidate = Path(payload.source_ref).expanduser().resolve(strict=True)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail="People source must be inside the configured people folder") from exc
        source = candidate
    else:
        try:
            source = resolve_approved_path(payload.source_ref)
        except (ValueError, PermissionError, FileNotFoundError) as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    items = []
    supported = unsupported = total_bytes = 0
    iterator = source.rglob("*") if payload.recursive else source.glob("*")
    for path in iterator:
        try:
            if not path.is_file():
                continue
            size = path.stat().st_size
            ok = path.suffix.lower() in SUPPORTED_EXTENSIONS
            total_bytes += size
            supported += int(ok)
            unsupported += int(not ok)
            if len(items) < 200:
                items.append({"relative_path": str(path.relative_to(source)), "size_bytes": size, "media_type": file_type(path), "supported": ok})
        except (OSError, ValueError):
            continue
    return {"source": str(source), "preview_only": True, "supported": supported, "unsupported": unsupported,
            "total_bytes": total_bytes, "sample": items, "sample_truncated": supported + unsupported > len(items),
            "note": "Preview only. This endpoint does not copy, modify, or process source files."}


@app.post(settings.api_prefix + "/ingest/jobs")
def create_ingest_job(payload: IngestPreviewRequest, db: Session = Depends(get_db)):
    if payload.dry_run:
        raise HTTPException(status_code=400, detail="Set dry_run=false after reviewing a preview to create a job")
    if payload.source_kind == "people_directory":
        source = Path(payload.source_ref).expanduser().resolve(strict=True)
        try:
            source.relative_to(settings.people_root.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=403, detail="People source must be inside configured people folder") from exc
    elif payload.source_kind == "upload_batch":
        raise HTTPException(status_code=400, detail="Upload batches are not yet implemented")
    else:
        try:
            source = resolve_approved_path(payload.source_ref)
        except (ValueError, PermissionError, FileNotFoundError) as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    job = create_job(db, payload.source_kind, str(source), payload.model_dump())
    # The separate worker polls this persisted queue and checkpoints per file.
    job.status = "queued"
    db.commit()
    return {"id": job.id, "status": job.status, "note": "Queued for the local worker. Check /api/v1/ingest/jobs for persisted progress."}


@app.get(settings.api_prefix + "/ingest/jobs")
def list_ingest_jobs(db: Session = Depends(get_db), limit: int = Query(50, ge=1, le=200)):
    rows = db.scalars(select(IngestJob).order_by(IngestJob.created_at.desc()).limit(limit)).all()
    return [{"id": x.id, "status": x.status, "source_kind": x.source_kind, "discovered": x.discovered, "processed": x.processed, "failed": x.failed, "created_at": x.created_at} for x in rows]


@app.get(settings.api_prefix + "/assets")
def list_assets(db: Session = Depends(get_db), q: str | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    stmt = select(Asset).order_by(Asset.created_at.desc())
    if q:
        like = f"%{q.replace('%', r'\%').replace('_', r'\_')}%"
        stmt = stmt.where(or_(Asset.original_name.ilike(like), Asset.relative_path.ilike(like), Asset.extracted_text.ilike(like)))
    rows = db.scalars(stmt.limit(limit).offset(offset)).all()
    return [{"id": x.id, "name": x.original_name, "path": x.relative_path, "media_type": x.media_type, "size_bytes": x.size_bytes,
             "width": x.width, "height": x.height, "duration_seconds": x.duration_seconds, "thumbnail_path": x.thumbnail_path,
             "tags": [{"label": t.label, "source": t.source, "confidence": t.confidence, "reviewed": t.reviewed} for t in x.tags]} for x in rows]


@app.post(settings.api_prefix + "/assets/{asset_id}/tags")
def add_tag(asset_id: int, payload: TagCreate, db: Session = Depends(get_db)):
    asset = db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    tag = Tag(asset_id=asset_id, label=payload.label.strip(), source=payload.source, confidence=payload.confidence, reviewed=payload.source == "manual")
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return {"id": tag.id, "asset_id": asset_id, "label": tag.label, "source": tag.source, "reviewed": tag.reviewed}


@app.post(settings.api_prefix + "/search")
def search(payload: SearchRequest, db: Session = Depends(get_db)):
    # Current safe fallback is lexical search. Vector search is a separate integration phase.
    q = payload.query.strip()
    stmt = select(Asset).where(or_(Asset.original_name.ilike(f"%{q}%"), Asset.relative_path.ilike(f"%{q}%"), Asset.extracted_text.ilike(f"%{q}%"))).limit(payload.limit)
    rows = db.scalars(stmt).all()
    return {"mode": "lexical", "embedding_model_loaded": embedding_service.model is not None,
            "results": [{"id": x.id, "name": x.original_name, "path": x.relative_path, "media_type": x.media_type} for x in rows],
            "note": "Semantic vector search activates only after local model integration and vector index are configured."}


@app.get(settings.api_prefix + "/people")
def list_people(db: Session = Depends(get_db)):
    rows = db.scalars(select(EmployeeProfile).order_by(EmployeeProfile.display_name)).all()
    return [{"id": x.id, "employee_code": x.employee_code, "display_name": x.display_name, "department": x.department,
             "profile_image_path": x.profile_image_path, "consent_recorded": x.consent_recorded} for x in rows]


@app.post(settings.api_prefix + "/people")
def add_person(payload: EmployeeProfileCreate, db: Session = Depends(get_db)):
    image_path = (settings.people_root / payload.profile_image_path).resolve()
    try:
        image_path.relative_to(settings.people_root.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Profile images must be inside the people folder") from exc
    if not image_path.is_file():
        raise HTTPException(status_code=404, detail="Profile image not found")
    if not payload.consent_recorded:
        raise HTTPException(status_code=400, detail="Record the appropriate employee notice/consent and authorization before registering a profile")
    person = EmployeeProfile(employee_code=payload.employee_code, display_name=payload.display_name, department=payload.department,
                             profile_image_path=str(image_path.relative_to(settings.people_root.resolve())), consent_recorded=True)
    db.add(person)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=409, detail="Employee code already exists or profile could not be saved")
    return {"id": person.id, "employee_code": person.employee_code, "display_name": person.display_name}


@app.get(settings.api_prefix + "/system/model-status")
def model_status():
    return {"embedding_provider": settings.embedding_provider, "embedding_model_path": str(settings.embedding_model_path),
            "embedding_model_loaded": embedding_service.model is not None, "semantic_search_ready": embedding_service.model is not None}
