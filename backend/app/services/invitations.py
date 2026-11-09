"""Magic-link invitations.

The token is a bearer credential, so it is treated like one:

- only its sha256 is stored (docs/07 gives `token_hash` UNIQUE for exactly this),
- it expires (72 hours),
- it is single use (`used_at`), and accepting it twice is a refusal, not a
  silently-valid session.

Accepting creates a `User` with no password for the invited address if none
exists. That is the whole point of the flow: the client is a principal with an
id, so `approve` can bind a signature to a person instead of to a request body.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AuditEvent, Invitation, Project, User

INVITE_TTL_HOURS = 72


class InvitationError(Exception):
    """Base for the refusals the router turns into 4xx."""


class InvitationUnknown(InvitationError):
    def __init__(self) -> None:
        super().__init__("this invitation does not exist")


class InvitationUsed(InvitationError):
    def __init__(self) -> None:
        super().__init__("this invitation has already been used")


class InvitationExpired(InvitationError):
    def __init__(self) -> None:
        super().__init__("this invitation has expired")


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _naive(moment: datetime) -> datetime:
    """SQLite hands back naive datetimes; comparing aware to naive raises."""
    return moment.replace(tzinfo=None) if moment.tzinfo else moment


def create(
    db: Session, *, project: Project, email: str, now: datetime
) -> tuple[Invitation, str]:
    token = secrets.token_urlsafe(32)
    expires_at = now + timedelta(hours=INVITE_TTL_HOURS)

    invitation = db.scalars(
        select(Invitation).where(
            Invitation.project_id == project.id, Invitation.email == email
        )
    ).one_or_none()
    if invitation is None:
        invitation = Invitation(project_id=project.id, email=email, token_hash=_digest(token))
        db.add(invitation)
    # Re-inviting replaces the token rather than adding a second live one, and
    # a used invitation becomes usable again for the new address holder.
    invitation.token_hash = _digest(token)
    invitation.expires_at = expires_at
    invitation.used_at = None

    db.add(
        AuditEvent(
            project_id=project.id,
            actor_id=project.owner_id,
            event_type="INVITATION_SENT",
            payload={"email": email, "expires_at": expires_at.isoformat()},
        )
    )
    db.commit()
    db.refresh(invitation)
    return invitation, token


def accept(db: Session, *, token: str, now: datetime) -> tuple[User, Invitation]:
    invitation = db.scalars(
        select(Invitation).where(Invitation.token_hash == _digest(token))
    ).one_or_none()
    if invitation is None:
        raise InvitationUnknown()
    if invitation.used_at is not None:
        raise InvitationUsed()
    if _naive(invitation.expires_at) < _naive(now):
        raise InvitationExpired()

    user = db.scalars(
        select(User).where(func.lower(User.email) == invitation.email.lower())
    ).one_or_none()
    if user is None:
        user = User(email=invitation.email, name="Client", password_hash=None)
        db.add(user)
        db.flush()

    invitation.used_at = now
    db.add(
        AuditEvent(
            project_id=invitation.project_id,
            actor_id=user.id,
            event_type="INVITATION_ACCEPTED",
            payload={"email": invitation.email},
        )
    )
    db.commit()
    db.refresh(user)
    return user, invitation


def accepted_for_project(db: Session, *, project_id: uuid.UUID, email: str):
    """The invitation that authorises this account on this project, or None."""
    return db.scalars(
        select(Invitation).where(
            Invitation.project_id == project_id,
            func.lower(Invitation.email) == email.lower(),
            Invitation.used_at.is_not(None),
        )
    ).one_or_none()
