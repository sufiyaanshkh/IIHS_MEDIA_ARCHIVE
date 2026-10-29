"""Durable job record helper. Worker execution is separate from request lifetime."""
from uuid import uuid4
from sqlalchemy.orm import Session
from ..models import IngestJob


def create_job(db: Session, source_kind: str, source_ref: str, options: dict) -> IngestJob:
    job = IngestJob(id=str(uuid4()), source_kind=source_kind, source_ref=source_ref, status="queued", options_json=options)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job
