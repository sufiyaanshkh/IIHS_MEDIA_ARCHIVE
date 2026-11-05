import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.database import get_db
from app.deps import csrf_check, current_user, roles
from app.models import (
    Annotation,
    AssetEmbedding,
    AuditEvent,
    FaceCandidateMatch,
    IngestionJob,
    MediaAsset,
    PersonProfile,
    SearchSegment,
    SourceDefinition,
    User,
    VideoFrame,
    now_utc,
)
from app.services import insights as insight_service
from app.services.hybrid_search import execute_hybrid_search
from app.services.media import MediaValidationError, resolve_under_root

router = APIRouter(prefix="/api/assets", tags=["media"])


class AssetSummary(BaseModel):
    id: str
    original_filename: str
    media_type: str
    detected_mime: str
    size_bytes: int
    width: int | None
    height: int | None
    duration_ms: int | None
    created_at: datetime
    annotations: list[dict]


def _cursor(raw: str | None):
    if not raw:
        return None
    try:
        value = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        return datetime.fromisoformat(value[0]), str(value[1])
    except (ValueError, TypeError, IndexError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="Invalid page cursor.") from None


def _encode_cursor(asset: MediaAsset):
    raw = json.dumps([asset.created_at.isoformat(), asset.id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _summary(asset: MediaAsset):
    return {
        "id": asset.id,
        "original_filename": asset.original_filename,
        "media_type": asset.media_type,
        "detected_mime": asset.detected_mime,
        "size_bytes": asset.size_bytes,
        "width": asset.width,
        "height": asset.height,
        "duration_ms": asset.duration_ms,
        "has_thumbnail": bool(asset.thumbnail_key),
        "created_at": asset.created_at,
        "source_path": asset.source_relative_key,
        "annotations": [
            {"id": a.id, "label": a.label, "source": a.source, "status": a.status, "confidence": a.confidence}
            for a in asset.annotations
        ],
    }


def _year_window(year: int | None):
    if year is None:
        return None
    return datetime(year, 1, 1, tzinfo=timezone.utc), datetime(year + 1, 1, 1, tzinfo=timezone.utc)


@router.get("")
def list_assets(
    user: Annotated[User, Depends(roles("admin", "archivist", "researcher"))],
    db: Annotated[Session, Depends(get_db)],
    q: str = Query(default="", max_length=200),
    media_type: str | None = Query(default=None, pattern="^(image|video|audio|document)$"),
    added_year: int | None = Query(default=None, ge=1970, le=2200),
    person_ids: str | None = Query(default=None, max_length=1000),
    cursor: str | None = None,
    limit: int = Query(default=30, ge=1, le=100),
):
    parsed_person_ids = [pid.strip() for pid in (person_ids or "").split(",") if pid.strip()]
    if parsed_person_ids:
        ranked = execute_hybrid_search(
            db,
            q=q,
            mode="hybrid",
            media_type=media_type,
            added_year=added_year,
            limit=limit,
            person_ids=parsed_person_ids,
        )
        return {
            "items": [_summary(asset) | meta for asset, meta in ranked],
            "next_cursor": None,
        }
    query = select(MediaAsset).options(joinedload(MediaAsset.annotations)).order_by(MediaAsset.created_at.desc(), MediaAsset.id.desc())
    if q.strip():
        escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        term = f"%{escaped}%"
        annotated_assets = select(Annotation.asset_id).where(Annotation.label.ilike(term))
        segment_assets = select(SearchSegment.asset_id).where(
            or_(SearchSegment.text_content.ilike(term), SearchSegment.title.ilike(term))
        )
        transcript = MediaAsset.metadata_json["transcription"]["text"].as_string()
        translation = MediaAsset.metadata_json["transcription"]["english_translation"].as_string()
        extracted_text = MediaAsset.metadata_json["text_extraction"]["text"].as_string()
        context_text = MediaAsset.metadata_json["context"]["context_text"].as_string()
        query = query.where(
            or_(
                MediaAsset.original_filename.ilike(term),
                MediaAsset.source_relative_key.ilike(term),
                transcript.ilike(term),
                translation.ilike(term),
                extracted_text.ilike(term),
                context_text.ilike(term),
                MediaAsset.id.in_(annotated_assets),
                MediaAsset.id.in_(segment_assets),
            )
        )
    if media_type:
        query = query.where(MediaAsset.media_type == media_type)
    if bounds := _year_window(added_year):
        query = query.where(MediaAsset.created_at >= bounds[0], MediaAsset.created_at < bounds[1])
    page = _cursor(cursor)
    if page:
        created_at, asset_id = page
        query = query.where(or_(MediaAsset.created_at < created_at, and_(MediaAsset.created_at == created_at, MediaAsset.id < asset_id)))
    rows = db.scalars(query.limit(limit + 1)).unique().all()
    more = len(rows) > limit
    rows = rows[:limit]
    return {"items": [_summary(row) for row in rows], "next_cursor": _encode_cursor(rows[-1]) if more and rows else None}


@router.get("/search")
def search_assets(
    user: Annotated[User, Depends(roles("admin", "archivist", "researcher"))],
    db: Annotated[Session, Depends(get_db)],
    q: str = Query(default="", max_length=200),
    mode: str = Query(default="keyword", pattern="^(keyword|semantic|hybrid)$"),
    media_type: str | None = Query(default=None, pattern="^(image|video|audio|document)$"),
    added_year: int | None = Query(default=None, ge=1970, le=2200),
    person_ids: str | None = Query(default=None, max_length=1000),
    cursor: str | None = None,
    limit: int = Query(default=30, ge=1, le=100),
):
    parsed_person_ids = [pid.strip() for pid in (person_ids or "").split(",") if pid.strip()]
    if not q.strip() and not parsed_person_ids:
        raise HTTPException(status_code=422, detail="Either query 'q' or 'person_ids' must be provided.")
    ranked = execute_hybrid_search(
        db,
        q=q,
        mode=mode,
        media_type=media_type,
        added_year=added_year,
        limit=limit,
        person_ids=parsed_person_ids or None,
    )
    if not ranked and mode == "keyword" and not parsed_person_ids:
        return list_assets(user, db, q=q, media_type=media_type, added_year=added_year, cursor=cursor, limit=limit)
    return {
        "items": [_summary(asset) | meta for asset, meta in ranked],
        "next_cursor": None,
    }


@router.get("/{asset_id}")
def get_asset(
    asset_id: str,
    user: Annotated[User, Depends(roles("admin", "archivist", "researcher"))],
    db: Annotated[Session, Depends(get_db)],
):
    asset = db.scalar(select(MediaAsset).options(joinedload(MediaAsset.annotations)).where(MediaAsset.id == asset_id))
    if asset is None:
        raise HTTPException(status_code=404, detail="Media record not found.")
    return _summary(asset) | {"metadata": asset.metadata_json}


@router.get("/{asset_id}/segments")
def list_asset_segments(
    asset_id: str,
    user: Annotated[User, Depends(roles("admin", "archivist", "researcher"))],
    db: Annotated[Session, Depends(get_db)],
):
    asset = db.get(MediaAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Media record not found.")
    segments = db.scalars(
        select(SearchSegment)
        .where(SearchSegment.asset_id == asset_id)
        .order_by(SearchSegment.segment_ordinal, SearchSegment.created_at)
    ).all()
    return [
        {
            "id": seg.id,
            "asset_id": seg.asset_id,
            "segment_type": seg.segment_type,
            "modality": seg.modality,
            "segment_ordinal": seg.segment_ordinal,
            "title": seg.title,
            "text_content": seg.text_content,
            "page_number": seg.page_number,
            "start_ms": seg.start_ms,
            "end_ms": seg.end_ms,
            "frame_id": seg.frame_id,
            "model_id": seg.model_id,
            "model_revision": seg.model_revision,
            "original_file_reference": seg.original_file_reference,
            "content_hash": seg.content_hash,
            "has_embedding": seg.embedding is not None,
            "metadata": seg.metadata_json,
            "created_at": seg.created_at,
        }
        for seg in segments
    ]


def _source_details(db: Session, asset: MediaAsset):
    source = db.get(SourceDefinition, asset.source_id) if asset.source_id else None
    job_id = (asset.metadata_json or {}).get("ingestion_job_id")
    job = db.get(IngestionJob, job_id) if job_id else None
    importer = db.get(User, job.requested_by) if job else None
    browser_upload = bool(source and source.name == "Browser file import")
    if browser_upload:
        source_name, source_path = "Uploaded from a computer (browser file picker)", None
    elif source:
        source_name, source_path = source.name, asset.source_relative_key
    else:
        source_name, source_path = "Source no longer configured", asset.source_relative_key
    return {
        "original_filename": asset.original_filename,
        "source_name": source_name,
        "source_kind": "browser_upload" if browser_upload else "demo_folder" if source and source.is_demo else "approved_folder" if source else "unknown",
        "source_path": source_path,
        "imported_by": importer.username if importer else None,
        "imported_at": asset.created_at,
        "ingestion_job_id": job_id,
        "sha256": asset.sha256,
        "mime": asset.detected_mime,
        "size_bytes": asset.size_bytes,
        "width": asset.width,
        "height": asset.height,
        "duration_ms": asset.duration_ms,
    }


def _image_vector(db: Session, asset_id: str):
    vectors = db.scalars(
        select(AssetEmbedding.embedding).where(
            AssetEmbedding.asset_id == asset_id,
            AssetEmbedding.modality.in_(["image", "image_frame"]),
            AssetEmbedding.model_id == settings.embedding_model,
            AssetEmbedding.model_revision == settings.embedding_revision,
        )
    ).all()
    if not vectors:
        vectors = db.scalars(
            select(SearchSegment.embedding).where(
                SearchSegment.asset_id == asset_id,
                SearchSegment.segment_type.in_(["image_visual", "video_frame"]),
                SearchSegment.embedding.is_not(None),
                SearchSegment.model_id == settings.embedding_model,
                SearchSegment.model_revision == settings.embedding_revision,
            )
        ).all()
    return insight_service.mean_image_vector([list(vector) for vector in vectors if vector is not None])


def _review_state(asset: MediaAsset):
    review = (asset.metadata_json or {}).get("review")
    if not review:
        return None
    pending = sum(1 for annotation in asset.annotations if annotation.source == "model" and annotation.status in {"suggested", "deferred"})
    status = "pending" if pending else "reviewed" if review.get("suggestions_created") else "requested"
    return review | {"status": status, "pending_suggestions": pending}


@router.get("/{asset_id}/insights")
def get_asset_insights(
    asset_id: str,
    user: Annotated[User, Depends(roles("admin", "archivist", "researcher"))],
    db: Annotated[Session, Depends(get_db)],
):
    asset = db.scalar(select(MediaAsset).options(joinedload(MediaAsset.annotations)).where(MediaAsset.id == asset_id))
    if asset is None:
        raise HTTPException(status_code=404, detail="Media record not found.")
    camera = {"fields": [], "gps_present": False}
    media_path: Path | None = None
    if asset.media_type == "image":
        try:
            media_path = _asset_media_path(db, asset)
            camera = insight_service.camera_metadata(media_path)
        except HTTPException:
            media_path = None
    vector = _image_vector(db, asset.id)
    return {
        "source": _source_details(db, asset),
        "camera": camera,
        "objects": insight_service.rank_objects(vector),
        "employees": insight_service.employees_status(db=db, asset=asset, media_path=media_path),
        "keywords": insight_service.rank_keywords(vector),
        "review": _review_state(asset),
    }


class ReviewRequestBody(BaseModel):
    comment: str | None = Field(default=None, max_length=2000)


@router.post("/{asset_id}/review")
def request_asset_review(
    asset_id: str,
    body: ReviewRequestBody,
    request: Request,
    actor: Annotated[User, Depends(roles("admin", "archivist", "researcher"))],
    db: Annotated[Session, Depends(get_db)],
    csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
    csrf_cookie: str | None = Cookie(default=None, alias="iihs_csrf"),
):
    """Send an asset's AI objects, IIHS keywords, and unverified face matches to the human review queue."""
    csrf_check(request, csrf_header, csrf_cookie)
    asset = db.scalar(select(MediaAsset).options(joinedload(MediaAsset.annotations)).where(MediaAsset.id == asset_id))
    if asset is None:
        raise HTTPException(status_code=404, detail="Media record not found.")
    current = _review_state(asset)
    if current and current["status"] == "pending":
        return {"review": current, "created": 0}
    vector = _image_vector(db, asset.id)
    existing = {annotation.label.casefold() for annotation in asset.annotations}
    created = 0
    for result, model_name in (
        (insight_service.rank_keywords(vector), insight_service.KEYWORD_MODEL_NAME),
        (insight_service.rank_objects(vector), insight_service.OBJECT_MODEL_NAME),
    ):
        for item in result["items"]:
            if item["label"].casefold() in existing:
                continue
            existing.add(item["label"].casefold())
            asset.annotations.append(
                Annotation(
                    label=item["label"][:160],
                    source="model",
                    status="suggested",
                    confidence=min(1.0, max(0.0, item["score"])),
                    model_name=model_name,
                    model_version=settings.embedding_revision,
                    created_by=actor.id,
                )
            )
            created += 1

    # Also queue any unverified InsightFace (buffalo_l) candidate matches on this asset for human review
    unverified_faces = db.execute(
        select(FaceCandidateMatch, PersonProfile)
        .join(PersonProfile, PersonProfile.id == FaceCandidateMatch.person_id)
        .where(
            FaceCandidateMatch.asset_id == asset.id,
            FaceCandidateMatch.status == "unverified",
            PersonProfile.status == "active",
        )
        .order_by(FaceCandidateMatch.aggregate_similarity.desc())
    ).all()
    for match, person in unverified_faces:
        if person.display_name.casefold() in existing:
            continue
        existing.add(person.display_name.casefold())
        asset.annotations.append(
            Annotation(
                label=person.display_name[:160],
                source="model",
                status="suggested",
                confidence=min(1.0, max(0.0, float(match.aggregate_similarity))),
                model_name=f"insightface/{settings.face_model_pack}",
                model_version="buffalo_l-w600k_r50",
                created_by=actor.id,
            )
        )
        created += 1
    asset.metadata_json = {
        **(asset.metadata_json or {}),
        "review": {
            "requested_by": actor.username,
            "requested_at": now_utc().isoformat(),
            "comment": (body.comment or "").strip() or None,
            "suggestions_created": created,
        },
    }
    db.add(
        AuditEvent(
            actor_id=actor.id,
            action="asset_review_requested",
            target_type="asset",
            target_id=asset.id,
            request_id=getattr(request.state, "request_id", None),
            detail={"suggestions_created": created},
        )
    )
    db.commit()
    db.refresh(asset)
    return {"review": _review_state(asset), "created": created}


def _media_path(storage_key: str) -> Path:
    root = settings.storage_root.resolve()
    path = (root / storage_key).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(status_code=404, detail="Media file is unavailable.")
    return path


def _asset_media_path(db: Session, asset: MediaAsset) -> Path:
    """Resolve media file from archive storage or read-only approved source root."""
    try:
        return _media_path(asset.storage_key)
    except HTTPException:
        if asset.source_id and asset.source_relative_key:
            source = db.get(SourceDefinition, asset.source_id)
            if source and source.enabled and source.root_path:
                try:
                    return resolve_under_root(Path(source.root_path), asset.source_relative_key)
                except (MediaValidationError, OSError):
                    pass
        raise


@router.get("/{asset_id}/media")
def get_media(
    asset_id: str,
    user: Annotated[User, Depends(roles("admin", "archivist", "researcher"))],
    db: Annotated[Session, Depends(get_db)],
):
    asset = db.get(MediaAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Media record not found.")
    path = _asset_media_path(db, asset)
    return FileResponse(
        path,
        media_type=asset.detected_mime,
        filename=f"{asset.id}.{path.suffix.lstrip('.')}",
        content_disposition_type="inline",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
    )


@router.get("/{asset_id}/thumbnail")
def get_thumbnail(
    asset_id: str,
    user: Annotated[User, Depends(roles("admin", "archivist", "researcher"))],
    db: Annotated[Session, Depends(get_db)],
):
    asset = db.get(MediaAsset, asset_id)
    if asset is None or not asset.thumbnail_key:
        raise HTTPException(status_code=404, detail="Thumbnail is unavailable.")
    return FileResponse(
        _media_path(asset.thumbnail_key),
        media_type="image/webp",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
    )


@router.get("/{asset_id}/frames")
def list_video_frames(
    asset_id: str,
    user: Annotated[User, Depends(roles("admin", "archivist", "researcher"))],
    db: Annotated[Session, Depends(get_db)],
):
    asset = db.get(MediaAsset, asset_id)
    if asset is None or asset.media_type != "video":
        raise HTTPException(status_code=404, detail="Video record not found.")
    return [
        {"id": frame.id, "timestamp_ms": frame.timestamp_ms}
        for frame in db.scalars(select(VideoFrame).where(VideoFrame.asset_id == asset.id).order_by(VideoFrame.timestamp_ms)).all()
    ]


@router.get("/{asset_id}/frames/{frame_id}")
def get_video_frame(
    asset_id: str,
    frame_id: str,
    user: Annotated[User, Depends(roles("admin", "archivist", "researcher"))],
    db: Annotated[Session, Depends(get_db)],
):
    frame = db.scalar(select(VideoFrame).where(VideoFrame.id == frame_id, VideoFrame.asset_id == asset_id))
    if frame is None:
        raise HTTPException(status_code=404, detail="Frame not found.")
    return FileResponse(
        _media_path(frame.storage_key),
        media_type="image/webp",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
    )
