import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import current_user, csrf_check, roles
from app.models import AuthSession, AuditEvent, LoginAttempt, User, UserRole
from app.security import create_session, hash_token, passwords

router = APIRouter(prefix="/api/auth", tags=["authentication"])
DUMMY_PASSWORD_HASH = passwords.hash(secrets.token_urlsafe(32))


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=120)
    password: str = Field(min_length=1, max_length=72)


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=120, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=72)
    role: UserRole


class BootstrapRequest(BaseModel):
    username: str = Field(min_length=3, max_length=120, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=72)
    setup_token: str = Field(min_length=32, max_length=256)


class RegistrationRequest(BaseModel):
    username: str = Field(min_length=3, max_length=120, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=72)


class RoleUpdate(BaseModel):
    role: UserRole


def public_user(user: User):
    return {"id": user.id, "username": user.username, "role": user.role.value, "created_at": user.created_at, "mode": "demo" if settings.demo_mode else "restricted"}


def _start_session(db: Session, user: User, response: Response):
    token, csrf = create_session(db, user)
    response.set_cookie("iihs_session", token, max_age=settings.session_hours * 3600, httponly=True, secure=settings.cookie_secure, samesite="strict", path="/")
    response.set_cookie("iihs_csrf", csrf, max_age=settings.session_hours * 3600, httponly=False, secure=settings.cookie_secure, samesite="strict", path="/")


def _attempt_ip(request: Request):
    return hashlib.sha256((request.client.host if request.client else "unknown").encode()).hexdigest()


@router.get("/bootstrap/status")
def bootstrap_status(db: Annotated[Session, Depends(get_db)]):
    has_users = bool(db.scalar(select(func.count()).select_from(User)))
    return {"can_create_admin": bool(settings.initial_admin_token) and not has_users, "can_register": settings.allow_self_registration and has_users}


@router.post("/bootstrap", status_code=201)
def bootstrap_admin(body: BootstrapRequest, request: Request, response: Response, db: Annotated[Session, Depends(get_db)]):
    if not settings.initial_admin_token or not secrets.compare_digest(body.setup_token, settings.initial_admin_token):
        raise HTTPException(status_code=403, detail="Administrator setup is unavailable or the setup key is invalid.")
    if db.bind.dialect.name == "postgresql":
        # Serialize first-admin creation so concurrent requests cannot both win.
        db.execute(text("SELECT pg_advisory_xact_lock(74211, 1)"))
    if db.scalar(select(func.count()).select_from(User)):
        raise HTTPException(status_code=409, detail="An administrator account already exists.")
    if len(body.password.encode("utf-8")) > 72:
        raise HTTPException(status_code=422, detail="Password must use no more than 72 UTF-8 bytes.")
    user = User(username=body.username.strip().lower(), password_hash=passwords.hash(body.password), role=UserRole.admin)
    db.add(user)
    db.flush()
    db.add(AuditEvent(actor_id=user.id, action="initial_admin_created", target_type="user", target_id=user.id))
    _start_session(db, user, response)
    return {"user": public_user(user)}


@router.post("/register", status_code=201)
def register(body: RegistrationRequest, request: Request, response: Response, db: Annotated[Session, Depends(get_db)]):
    if not settings.allow_self_registration:
        raise HTTPException(status_code=403, detail="Self-registration is disabled.")
    if not db.scalar(select(func.count()).select_from(User)):
        raise HTTPException(status_code=409, detail="Administrator setup must be completed first.")
    if len(body.password.encode("utf-8")) > 72:
        raise HTTPException(status_code=422, detail="Password must use no more than 72 UTF-8 bytes.")
    ip_hash = _attempt_ip(request)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
    db.execute(delete(LoginAttempt).where(LoginAttempt.attempted_at < cutoff))
    recent = db.scalars(select(LoginAttempt.id).where(LoginAttempt.ip_hash == ip_hash, LoginAttempt.purpose == "register", LoginAttempt.attempted_at >= cutoff).limit(10)).all()
    if len(recent) >= 10:
        db.commit()
        raise HTTPException(status_code=429, detail="Too many account creation attempts. Try again later.")
    db.add(LoginAttempt(ip_hash=ip_hash, purpose="register"))
    username = body.username.strip().lower()
    if db.scalar(select(User.id).where(User.username == username)):
        db.commit()
        raise HTTPException(status_code=409, detail="That username is unavailable.")
    user = User(username=username, password_hash=passwords.hash(body.password), role=UserRole.researcher)
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="That username is unavailable.") from None
    db.add(AuditEvent(actor_id=user.id, action="self_registration", target_type="user", target_id=user.id, detail={"role": UserRole.researcher.value}))
    _start_session(db, user, response)
    return {"user": public_user(user)}


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response, db: Annotated[Session, Depends(get_db)]):
    ip_hash = _attempt_ip(request)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
    db.execute(delete(LoginAttempt).where(LoginAttempt.attempted_at < cutoff))
    recent_attempts = db.scalars(select(LoginAttempt.id).where(LoginAttempt.ip_hash == ip_hash, LoginAttempt.purpose == "login", LoginAttempt.attempted_at >= cutoff).limit(10)).all()
    if len(recent_attempts) >= 10:
        db.commit()
        raise HTTPException(status_code=429, detail="Too many sign-in attempts. Try again later.")
    user = db.scalar(select(User).where(User.username == body.username.strip().lower(), User.active.is_(True)))
    valid = False
    try:
        valid = passwords.verify(body.password, user.password_hash if user else DUMMY_PASSWORD_HASH)
    except (ValueError, TypeError):
        valid = False
    if not valid:
        db.add(LoginAttempt(ip_hash=ip_hash, purpose="login"))
        db.add(AuditEvent(action="login_failed", target_type="user", result="failure", request_id=getattr(request.state, "request_id", None)))
        db.commit()
        raise HTTPException(status_code=401, detail="Username or password is incorrect.")
    db.execute(delete(LoginAttempt).where(LoginAttempt.ip_hash == ip_hash, LoginAttempt.purpose == "login"))
    _start_session(db, user, response)
    db.add(AuditEvent(actor_id=user.id, action="login", target_type="user", target_id=user.id, request_id=getattr(request.state, "request_id", None)))
    db.commit()
    return {"user": public_user(user)}


@router.post("/logout")
def logout(response: Response, request: Request, user: Annotated[User, Depends(current_user)], db: Annotated[Session, Depends(get_db)], session: str | None = Cookie(default=None, alias="iihs_session"), csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"), csrf_cookie: str | None = Cookie(default=None, alias="iihs_csrf")):
    csrf_check(request, csrf_header, csrf_cookie)
    if session:
        db.query(AuthSession).filter(AuthSession.token_hash == hash_token(session)).delete(synchronize_session=False)
    db.add(AuditEvent(actor_id=user.id, action="logout", target_type="user", target_id=user.id, request_id=getattr(request.state, "request_id", None)))
    db.commit()
    response.delete_cookie("iihs_session", path="/", secure=settings.cookie_secure, httponly=True, samesite="strict")
    response.delete_cookie("iihs_csrf", path="/", secure=settings.cookie_secure, httponly=False, samesite="strict")
    return {"status": "signed out"}


@router.get("/me")
def me(user: Annotated[User, Depends(current_user)]):
    return public_user(user)


@router.post("/users", status_code=201)
def create_user(body: UserCreate, request: Request, user: Annotated[User, Depends(roles("admin"))], db: Annotated[Session, Depends(get_db)], csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"), csrf_cookie: str | None = Cookie(default=None, alias="iihs_csrf")):
    csrf_check(request, csrf_header, csrf_cookie)
    name = body.username.strip().lower()
    if db.scalar(select(User.id).where(User.username == name)):
        raise HTTPException(status_code=409, detail="That username already exists.")
    if len(body.password.encode("utf-8")) > 72:
        raise HTTPException(status_code=422, detail="Password must use no more than 72 UTF-8 bytes.")
    created = User(username=name, password_hash=passwords.hash(body.password), role=body.role)
    db.add(created)
    db.flush()
    db.add(AuditEvent(actor_id=user.id, action="user_created", target_type="user", target_id=created.id, detail={"role": body.role.value}))
    db.commit()
    return public_user(created)


@router.get("/users")
def list_users(user: Annotated[User, Depends(roles("admin"))], db: Annotated[Session, Depends(get_db)]):
    return [public_user(row) | {"active": row.active} for row in db.scalars(select(User).order_by(User.username)).all()]


@router.post("/users/{user_id}/role")
def update_user_role(user_id: str, body: RoleUpdate, request: Request, actor: Annotated[User, Depends(roles("admin"))], db: Annotated[Session, Depends(get_db)], csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"), csrf_cookie: str | None = Cookie(default=None, alias="iihs_csrf")):
    csrf_check(request, csrf_header, csrf_cookie)
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    if user.id == actor.id and body.role != UserRole.admin:
        raise HTTPException(status_code=409, detail="You cannot remove your own administrator role.")
    previous = user.role
    if previous != body.role:
        user.role = body.role
        db.query(AuthSession).filter(AuthSession.user_id == user.id).delete(synchronize_session=False)
        db.add(AuditEvent(actor_id=actor.id, action="user_role_changed", target_type="user", target_id=user.id, detail={"from": previous.value, "to": body.role.value}))
        db.commit()
    return public_user(user)


@router.post("/users/{user_id}/revoke")
def revoke_user(user_id: str, request: Request, actor: Annotated[User, Depends(roles("admin"))], db: Annotated[Session, Depends(get_db)], csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"), csrf_cookie: str | None = Cookie(default=None, alias="iihs_csrf")):
    csrf_check(request, csrf_header, csrf_cookie)
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    if user.id == actor.id:
        raise HTTPException(status_code=409, detail="You cannot revoke your own account.")
    user.active = False
    db.query(AuthSession).filter(AuthSession.user_id == user.id).delete(synchronize_session=False)
    db.add(AuditEvent(actor_id=actor.id, action="user_revoked", target_type="user", target_id=user.id))
    db.commit()
    return {"status": "revoked"}
