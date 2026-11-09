"""Agreement routes: draft, read, send, approve, reject.

Both prefixes live here because they are one resource seen from two sides:
`POST /projects/{id}/agreement` creates a version, the rest act on a version id.

Every refusal is a named code, not a generic 409, because a client has to be
able to tell "your milestones do not add up" from "you are approving an old
version" without parsing prose (docs/08 §4).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.ai.client import AiInvalidOutput, AiInvalidRequest, AiUnavailable
from app.ai.client import get_client as get_ai_client
from app.database import get_db
from app.routers.auth import CurrentUser
from app.schemas.agreements import (
    AgreementCreate,
    ApproveRequest,
    MilestoneOut,
    RejectRequest,
    SectionOut,
    VersionOut,
)
from app.services import agreements as agreement_service
from app.services import projects as project_service
from app.services.ai_runs import make_recorder

router = APIRouter(tags=["agreements"])

DbSession = Annotated[Session, Depends(get_db)]


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "NOT_FOUND", "message": "Agreement not found."},
    )


def _out(version) -> VersionOut:
    """`routers/` may not import `models/` (tests/test_boundaries.py), so the
    parameter is deliberately untyped here: the shape is enforced by
    `VersionOut`, which is the only contract a caller sees."""
    return VersionOut(
        id=version.id,
        version_no=version.version_no,
        status=version.status,
        budget_amount=version.budget_amount,
        content_hash=version.content_hash,
        content_intact=agreement_service.content_hash_for(version) == version.content_hash,
        created_at=version.created_at,
        freelancer_approved_at=version.freelancer_approved_at,
        client_approved_at=version.client_approved_at,
        sections=[SectionOut.model_validate(s) for s in version.sections],
        milestones=[MilestoneOut.model_validate(m) for m in version.milestones],
    )


def _ai_failure(exc: Exception) -> HTTPException:
    """Same mapping as `/analyse`: an outage is a 503, a rejection is a 502, and
    an untrustworthy output is a 502 the caller can retry."""
    if isinstance(exc, AiUnavailable):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "AI_UNAVAILABLE",
                "message": "The drafting service is unavailable. Nothing was written.",
            },
        )
    if isinstance(exc, AiInvalidRequest):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "AI_INVALID_REQUEST",
                "message": "The drafting service rejected the request.",
            },
        )
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail={
            "code": "AI_INVALID_OUTPUT",
            "message": (
                "The draft could not be trusted — its milestones did not add up to the "
                "budget — and was discarded. Nothing was written."
            ),
        },
    )


def _refusal(exc: agreement_service.AgreementError) -> HTTPException:
    if isinstance(exc, agreement_service.MilestoneSumMismatch):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "MILESTONE_SUM_MISMATCH",
                "message": (
                    f"Milestones total {exc.total:.2f} but the budget is {exc.budget:.2f}."
                ),
                "details": {
                    "budget": f"{exc.budget:.2f}",
                    "sum": f"{exc.total:.2f}",
                    "delta": f"{exc.total - exc.budget:.2f}",
                },
            },
        )
    if isinstance(exc, agreement_service.StaleVersion):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "STALE_VERSION",
                "message": (
                    f"Version {exc.approved} is not the current version "
                    f"(current is {exc.current})."
                ),
                "details": {"approved_version": exc.approved, "current_version": exc.current},
            },
        )
    if isinstance(exc, agreement_service.VersionRace):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "VERSION_RACE",
                "message": "Another draft was created at the same time. Reload and retry.",
            },
        )
    if isinstance(exc, agreement_service.ContentChanged):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "CONTENT_CHANGED",
                "message": "This version changed after it was hashed. Draft a new version.",
            },
        )
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": "INVALID_STATE", "message": str(exc)},
    )


@router.post(
    "/projects/{project_id}/agreement",
    response_model=VersionOut,
    status_code=status.HTTP_201_CREATED,
)
def create_agreement(
    project_id: uuid.UUID, payload: AgreementCreate, user: CurrentUser, db: DbSession
) -> VersionOut:
    project = project_service.get_for_owner(db, owner_id=user.id, project_id=project_id)
    if project is None:
        raise _not_found()

    client = get_ai_client(record=make_recorder(db))
    try:
        draft = agreement_service.draft_agreement(
            requirements=payload.accepted_requirements,
            budget=project.budget_amount,
            currency=project.currency,
            client=client,
            project_id=project.id,
        )
    except (AiUnavailable, AiInvalidRequest, AiInvalidOutput) as exc:
        raise _ai_failure(exc) from None

    try:
        version = agreement_service.create_version(
            db, project=project, draft=draft, author_id=user.id
        )
    except agreement_service.VersionRace as exc:
        raise _refusal(exc) from None
    return _out(version)


@router.get("/agreements/{version_id}", response_model=VersionOut)
def get_agreement(version_id: uuid.UUID, user: CurrentUser, db: DbSession) -> VersionOut:
    version = agreement_service.get_for_owner(db, owner_id=user.id, version_id=version_id)
    if version is None:
        raise _not_found()
    return _out(version)


@router.post("/agreements/{version_id}/send", response_model=VersionOut)
def send_agreement(version_id: uuid.UUID, user: CurrentUser, db: DbSession) -> VersionOut:
    version = agreement_service.get_for_owner(db, owner_id=user.id, version_id=version_id)
    if version is None:
        raise _not_found()
    try:
        return _out(agreement_service.send(db, version=version, actor_id=user.id))
    except agreement_service.AgreementError as exc:
        raise _refusal(exc) from None


@router.post("/agreements/{version_id}/approve", response_model=VersionOut)
def approve_agreement(
    version_id: uuid.UUID, payload: ApproveRequest, user: CurrentUser, db: DbSession
) -> VersionOut:
    version = agreement_service.get_for_owner(db, owner_id=user.id, version_id=version_id)
    if version is None:
        raise _not_found()
    try:
        return _out(
            agreement_service.approve(
                db, version=version, role=payload.role, actor_id=user.id, now=datetime.now(UTC)
            )
        )
    except agreement_service.AgreementError as exc:
        raise _refusal(exc) from None


@router.post("/agreements/{version_id}/reject", response_model=VersionOut)
def reject_agreement(
    version_id: uuid.UUID, payload: RejectRequest, user: CurrentUser, db: DbSession
) -> VersionOut:
    version = agreement_service.get_for_owner(db, owner_id=user.id, version_id=version_id)
    if version is None:
        raise _not_found()
    try:
        return _out(
            agreement_service.reject(
                db, version=version, reason=payload.reason, actor_id=user.id
            )
        )
    except agreement_service.AgreementError as exc:
        raise _refusal(exc) from None
