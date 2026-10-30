import re
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import roles, csrf_check
from app.models import AuditEvent, Tag, User

router = APIRouter(prefix="/api/tags", tags=["tags"])


class TagCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=1000)


@router.get("")
def list_tags(user: Annotated[User, Depends(roles("admin", "archivist", "researcher"))], db: Annotated[Session, Depends(get_db)]):
    return [{"id": tag.id, "canonical_name": tag.canonical_name, "display_name": tag.display_name, "description": tag.description, "active": tag.active} for tag in db.scalars(select(Tag).where(Tag.active.is_(True)).order_by(Tag.canonical_name)).all()]


@router.post("", status_code=201)
def create_tag(body: TagCreate, request: Request, actor: Annotated[User, Depends(roles("admin", "archivist"))], db: Annotated[Session, Depends(get_db)], csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"), csrf_cookie: str | None = Cookie(default=None, alias="iihs_csrf")):
    csrf_check(request, csrf_header, csrf_cookie)
    display = " ".join(body.display_name.split())
    canonical = display.casefold()
    if not re.fullmatch(r"[\w -]{1,160}", canonical, flags=re.UNICODE):
        raise HTTPException(status_code=422, detail="Tag contains unsupported characters.")
    if db.scalar(select(Tag.id).where(Tag.canonical_name == canonical)):
        raise HTTPException(status_code=409, detail="That tag already exists.")
    tag = Tag(canonical_name=canonical, display_name=display, description=body.description.strip() if body.description else None, created_by=actor.id)
    db.add(tag)
    db.flush()
    db.add(AuditEvent(actor_id=actor.id, action="tag_created", target_type="tag", target_id=tag.id, request_id=getattr(request.state, "request_id", None)))
    db.commit()
    return {"id": tag.id, "canonical_name": tag.canonical_name, "display_name": tag.display_name, "description": tag.description, "active": tag.active}
