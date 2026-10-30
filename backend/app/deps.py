import secrets
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.security import authenticate


def current_user(request: Request, db: Annotated[Session, Depends(get_db)], session: str | None = Cookie(default=None, alias="iihs_session")) -> User:
    user = authenticate(db, session)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return user


def roles(*allowed: str):
    def check(user: Annotated[User, Depends(current_user)]) -> User:
        if user.role.value not in allowed:
            raise HTTPException(status_code=403, detail="You are not permitted to perform this action.")
        return user
    return check


def csrf_check(request: Request, csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"), csrf_cookie: str | None = Cookie(default=None, alias="iihs_csrf")):
    if not csrf_header or not csrf_cookie or not secrets.compare_digest(csrf_header, csrf_cookie):
        raise HTTPException(status_code=403, detail="CSRF validation failed.")


def csrf_if_authenticated(request: Request, csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"), csrf_cookie: str | None = Cookie(default=None, alias="iihs_csrf")):
    if request.cookies.get("iihs_session"):
        csrf_check(request, csrf_header, csrf_cookie)
