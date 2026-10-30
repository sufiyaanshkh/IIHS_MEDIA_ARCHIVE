import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AuthSession, User

passwords = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(db: Session, user: User) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    db.add(AuthSession(token_hash=hash_token(token), user_id=user.id, expires_at=datetime.now(timezone.utc) + timedelta(hours=settings.session_hours)))
    db.commit()
    return token, csrf


def authenticate(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    session = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(token), AuthSession.expires_at > datetime.now(timezone.utc)))
    if session is None or not session.user.active:
        return None
    return session.user
