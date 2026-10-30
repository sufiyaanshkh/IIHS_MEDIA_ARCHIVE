import getpass
import re

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import AuditEvent, User, UserRole
from app.security import passwords


def main():
    username = input("Initial administrator username: ").strip().lower()
    if not re.fullmatch(r"[a-z0-9_.-]{3,120}", username):
        raise SystemExit("Use 3–120 letters, numbers, dots, underscores or hyphens.")
    password = getpass.getpass("Initial administrator password (8+ characters, max 72 UTF-8 bytes): ")
    confirm = getpass.getpass("Confirm password: ")
    if len(password) < 8 or len(password.encode("utf-8")) > 72 or password != confirm:
        raise SystemExit("Passwords must match, contain at least 8 characters and use no more than 72 UTF-8 bytes.")
    with SessionLocal.begin() as db:
        if db.scalar(select(func.count()).select_from(User)):
            raise SystemExit("An account already exists; use an administrator account to add users.")
        user = User(username=username, password_hash=passwords.hash(password), role=UserRole.admin)
        db.add(user)
        db.flush()
        db.add(AuditEvent(actor_id=user.id, action="initial_admin_created", target_type="user", target_id=user.id))
    print("Initial administrator created.")


if __name__ == "__main__":
    main()
