"""Invitation routes: the owner sends, the client accepts.

`GET /invite/{token}` is the link a person clicks, so it is a GET with an effect
— the usual magic-link compromise. It redirects to the page rather than returning
JSON, because the click comes from an email client, not from our JavaScript.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.routers.auth import CurrentUser, set_session
from app.schemas.auth import Email, UserOut
from app.services import invitations as invitation_service
from app.services import projects as project_service

router = APIRouter(tags=["invitations"])

DbSession = Annotated[Session, Depends(get_db)]


class InviteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Email


def _base() -> str:
    return settings.allowed_origins[0].rstrip("/")


@router.post("/projects/{project_id}/invitations", status_code=201)
def invite(
    project_id: uuid.UUID, payload: InviteCreate, user: CurrentUser, db: DbSession
) -> dict:
    project = project_service.get_for_owner(db, owner_id=user.id, project_id=project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Project not found."},
        )

    try:
        invitation, token = invitation_service.create(
            db, project=project, email=str(payload.email), now=datetime.now(UTC)
        )
    except invitation_service.SelfInvitation:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "SELF_INVITATION",
                "message": "You cannot invite your own address as the client.",
            },
        ) from None
    return {
        "email": invitation.email,
        "expires_at": invitation.expires_at,
        "invite_url": f"{_base()}/invite/{token}",
    }


def _accept(db: Session, token: str) -> UserOut:
    try:
        user, _ = invitation_service.accept(db, token=token, now=datetime.now(UTC))
    except invitation_service.InvitationUnknown:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "This invitation does not exist."},
        ) from None
    except invitation_service.InvitationUsed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "INVITATION_USED", "message": "This invitation has already been used."},
        ) from None
    except invitation_service.EmailAlreadyRegistered:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "EMAIL_ALREADY_REGISTERED",
                "message": (
                    "This address already has an account. Sign in with that account and "
                    "approve as the client; the invitation is already recorded against it."
                ),
            },
        ) from None
    except invitation_service.InvitationExpired:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "INVITATION_EXPIRED", "message": "This invitation has expired."},
        ) from None
    return UserOut.model_validate(user)


@router.post("/invitations/{token}/accept", response_model=UserOut)
def accept(token: str, db: DbSession, response: Response) -> UserOut:
    user = _accept(db, token)
    set_session(response, user.id)
    return user


@router.get("/invite/{token}", include_in_schema=False)
def accept_from_email(token: str, db: DbSession) -> RedirectResponse:
    """The link in the email. Same effect, then back to the page."""
    response = RedirectResponse("/?invite=accepted", status_code=303)
    try:
        user = _accept(db, token)
    except HTTPException as exc:
        code = (exc.detail or {}).get("code", "INVITATION_INVALID")
        return RedirectResponse(f"/?invite={code}", status_code=303)
    set_session(response, user.id)
    return response
