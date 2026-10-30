from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi import HTTPException
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import roles
from app.config import settings
from app.models import Annotation, AssetEmbedding, AuditEvent, IngestionJob, MediaAsset, User
from app.services.embeddings import model_health

router = APIRouter(prefix="/api/ops", tags=["operations"])


@router.get("/summary")
def summary(user: Annotated[User, Depends(roles("admin", "archivist", "researcher", "operator"))], db: Annotated[Session, Depends(get_db)]):
    return {
        "assets": db.scalar(select(func.count()).select_from(MediaAsset)) or 0,
        "images": db.scalar(select(func.count()).select_from(MediaAsset).where(MediaAsset.media_type == "image")) or 0,
        "videos": db.scalar(select(func.count()).select_from(MediaAsset).where(MediaAsset.media_type == "video")) or 0,
        "indexed_assets": db.scalar(select(func.count(distinct(AssetEmbedding.asset_id))).select_from(AssetEmbedding)) or 0,
        "pending_reviews": db.scalar(select(func.count()).select_from(Annotation).where(Annotation.status == "suggested")) or 0,
        "queued_jobs": db.scalar(select(func.count()).select_from(IngestionJob).where(IngestionJob.state == "queued")) or 0,
        "running_jobs": db.scalar(select(func.count()).select_from(IngestionJob).where(IngestionJob.state.in_(["running", "cancel_requested"]))) or 0,
        "recent_activity": [{"action": row.action, "target_type": row.target_type, "created_at": row.created_at} for row in db.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(10)).all()],
    }


@router.get("/audit")
def audit_events(user: Annotated[User, Depends(roles("admin", "operator"))], db: Annotated[Session, Depends(get_db)], limit: int = 50):
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 100.")
    return [{"id": row.id, "actor_id": row.actor_id, "action": row.action, "target_type": row.target_type, "target_id": row.target_id, "result": row.result, "created_at": row.created_at} for row in db.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit)).all()]


@router.get("/capabilities")
def capabilities(user: Annotated[User, Depends(roles("admin", "operator"))]):
    model = model_health()
    return {"mode": "demo" if settings.demo_mode else "restricted", "local_ai": model["status"], "model": model.get("model"), "semantic_search": "available" if model["status"] == "ready" else "unavailable", "video_processing": "available_if_ffmpeg_is_installed", "face_enrollment": "disabled"}
