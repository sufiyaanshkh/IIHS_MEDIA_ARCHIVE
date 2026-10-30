from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.deps import roles, csrf_check
from app.models import Annotation, AuditEvent, MediaAsset, ReviewEvent, Tag, User, now_utc

router = APIRouter(prefix="/api", tags=["annotations and review"])


class AnnotationCreate(BaseModel):
    label: str = Field(min_length=1, max_length=160)


class ReviewRequest(BaseModel):
    decision: str = Field(pattern="^(accept|reject|correct|defer)$")
    final_label: str | None = Field(default=None, max_length=160)
    comment: str | None = Field(default=None, max_length=2000)


@router.get("/review/queue")
def review_queue(user: Annotated[User, Depends(roles("admin", "archivist"))], db: Annotated[Session, Depends(get_db)], limit: int = Query(default=50, ge=1, le=100)):
    rows = db.scalars(select(Annotation).options(joinedload(Annotation.asset)).where(Annotation.status == "suggested").order_by(Annotation.created_at).limit(limit)).unique().all()
    return [{"id": row.id, "asset_id": row.asset_id, "filename": row.asset.original_filename, "label": row.label, "source": row.source, "confidence": row.confidence, "media_type": row.asset.media_type} for row in rows]


@router.post("/assets/{asset_id}/annotations", status_code=201)
def create_annotation(asset_id: str, body: AnnotationCreate, request: Request, actor: Annotated[User, Depends(roles("admin", "archivist"))], db: Annotated[Session, Depends(get_db)], csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"), csrf_cookie: str | None = Cookie(default=None, alias="iihs_csrf")):
    csrf_check(request, csrf_header, csrf_cookie)
    asset = db.get(MediaAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Media record not found.")
    label = body.label.strip()
    if not label:
        raise HTTPException(status_code=422, detail="Label cannot be empty.")
    canonical = " ".join(label.split()).casefold()
    tag = db.scalar(select(Tag).where(Tag.canonical_name == canonical))
    if tag is None:
        tag = Tag(canonical_name=canonical, display_name=" ".join(label.split()), created_by=actor.id)
        db.add(tag)
        db.flush()
    annotation = Annotation(asset_id=asset.id, tag_id=tag.id, label=label, source="human", status="accepted", created_by=actor.id, reviewed_by=actor.id, reviewed_at=now_utc())
    db.add(annotation)
    db.flush()
    db.add(ReviewEvent(annotation_id=annotation.id, reviewer_id=actor.id, decision="accept", previous_label=label, final_label=label))
    db.add(AuditEvent(actor_id=actor.id, action="annotation_created", target_type="annotation", target_id=annotation.id, request_id=getattr(request.state, "request_id", None)))
    db.commit()
    return {"id": annotation.id, "asset_id": asset.id, "label": label, "source": annotation.source, "status": annotation.status}


@router.post("/annotations/{annotation_id}/review")
def review_annotation(annotation_id: str, body: ReviewRequest, request: Request, actor: Annotated[User, Depends(roles("admin", "archivist"))], db: Annotated[Session, Depends(get_db)], csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"), csrf_cookie: str | None = Cookie(default=None, alias="iihs_csrf")):
    csrf_check(request, csrf_header, csrf_cookie)
    annotation = db.get(Annotation, annotation_id)
    if annotation is None:
        raise HTTPException(status_code=404, detail="Annotation not found.")
    if annotation.status not in {"suggested", "deferred"}:
        raise HTTPException(status_code=409, detail="This annotation has already been reviewed.")
    old_label = annotation.label
    if body.decision == "correct":
        final_label = (body.final_label or "").strip()
        if not final_label:
            raise HTTPException(status_code=422, detail="A corrected label is required.")
        annotation.label = final_label
        canonical = " ".join(final_label.split()).casefold()
        tag = db.scalar(select(Tag).where(Tag.canonical_name == canonical))
        if tag is None:
            tag = Tag(canonical_name=canonical, display_name=" ".join(final_label.split()), created_by=actor.id)
            db.add(tag)
            db.flush()
        annotation.tag_id = tag.id
        annotation.status = "corrected"
    elif body.decision == "accept":
        annotation.status = "accepted"
        final_label = annotation.label
    elif body.decision == "reject":
        annotation.status = "rejected"
        final_label = None
    else:
        annotation.status = "deferred"
        final_label = annotation.label
    annotation.reviewed_by = actor.id
    annotation.reviewed_at = now_utc()
    db.add(ReviewEvent(annotation_id=annotation.id, reviewer_id=actor.id, decision=body.decision, previous_label=old_label, final_label=final_label, comment=body.comment))
    db.add(AuditEvent(actor_id=actor.id, action=f"annotation_{body.decision}", target_type="annotation", target_id=annotation.id, request_id=getattr(request.state, "request_id", None)))
    db.commit()
    return {"id": annotation.id, "label": annotation.label, "status": annotation.status, "reviewed_at": annotation.reviewed_at}


@router.get("/annotations/{annotation_id}/history")
def review_history(annotation_id: str, user: Annotated[User, Depends(roles("admin", "archivist", "researcher"))], db: Annotated[Session, Depends(get_db)]):
    if db.get(Annotation, annotation_id) is None:
        raise HTTPException(status_code=404, detail="Annotation not found.")
    return [{"decision": event.decision, "previous_label": event.previous_label, "final_label": event.final_label, "comment": event.comment, "reviewer_id": event.reviewer_id, "created_at": event.created_at} for event in db.scalars(select(ReviewEvent).where(ReviewEvent.annotation_id == annotation_id).order_by(ReviewEvent.created_at)).all()]
