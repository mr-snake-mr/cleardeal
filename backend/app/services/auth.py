"""Registration and login.

Business rules live here, not in the router (docs/01 §7). The rules that matter:

- A duplicate email is a `409`, not a `500` from a unique-constraint violation
  leaking through. The check and the insert are in one transaction, and the
  constraint is still the backstop for the race between them.
- A failed login returns the same error whether the address is unknown or the
  password is wrong. Distinguishing them turns the login form into an account
  enumeration oracle.
- `password_hash` is never returned, never logged.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import User
from app.security import hash_password, verify_password


class EmailAlreadyRegistered(Exception):
    """Kept in one place so the router maps it to a single `409` code."""

    def __init__(self, email: str) -> None:
        super().__init__(email)
        self.email = email


def register(db: Session, *, email: str, name: str, password: str) -> User:
    existing = db.execute(select(User.id).where(User.email == email)).scalar_one_or_none()
    if existing is not None:
        raise EmailAlreadyRegistered(email)

    user = User(email=email, name=name, password_hash=hash_password(password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        # Two registrations racing the same address: the constraint wins, and
        # the caller sees the same 409 as the slow path rather than a 500.
        db.rollback()
        raise EmailAlreadyRegistered(email) from exc
    db.refresh(user)
    return user


def authenticate(db: Session, *, email: str, password: str) -> User | None:
    """Return the user on success, None on any failure — including unknown email."""
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None:
        # Still hash once so a missing account costs the same wall-clock time as
        # a wrong password. Without this the response time is an oracle.
        verify_password(password, "$".join(["pbkdf2_sha256", "600000", "AAAA", "AAAA"]))
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user
