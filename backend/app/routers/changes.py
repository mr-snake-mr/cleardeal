"""Scope-check routes: POST a request, read the analysis back.

The two refusals that matter are `NO_APPROVED_VERSION` (there is nothing to
compare against) and the AI mapping: a provider outage is a 503 that wrote
nothing, and a rejected output is a 502 — neither may create a half-analysed
request that looks like a decision.
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
from app.schemas.changes import ChangeCheckOut, ChangeCreate, ChangeItemOut, ChangeRequestOut
from app.services import changes as change_service
from app.services import projects as project_service
from app.services.ai_runs import make_recorder

router = APIRouter(tags=["changes"])

DbSession = Annotated[Session, Depends(get_db)]


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "NOT_FOUND", "message": "Change request not found."},
    )


def _out(request, items) -> ChangeCheckOut:
    return ChangeCheckOut(
        change_request=ChangeRequestOut.model_validate(request),
        items=[ChangeItemOut.model_validate(item) for item in items],
    )


@router.post(
    "/projects/{project_id}/changes", response_model=ChangeCheckOut, status_code=201
)
def check_change(
    project_id: uuid.UUID, payload: ChangeCreate, user: CurrentUser, db: DbSession
) -> ChangeCheckOut:
    project = project_service.get_for_owner(db, owner_id=user.id, project_id=project_id)
    if project is None:
        raise _not_found()

    version = change_service.current_version(db, project_id)
    if version is None or version.status != "APPROVED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "NO_APPROVED_VERSION",
                "message": "This project has no approved agreement to check against.",
            },
        )

    sections, section_ids = change_service.sections_for(db, version.id)
    client = get_ai_client(record=make_recorder(db))
    try:
        resolved = change_service.analyse_change(
            client=client, raw_text=payload.raw_text, sections=sections, project_id=project.id
        )
    except AiUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "AI_UNAVAILABLE",
                "message": "The check could not run. Nothing was recorded; try again.",
            },
        ) from None
    except (AiInvalidRequest, AiInvalidOutput):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "AI_INVALID_OUTPUT",
                "message": (
                    "The analysis could not be trusted and was discarded. "
                    "Nothing was recorded."
                ),
            },
        ) from None

    request, items = change_service.create_request(
        db,
        project=project,
        version=version,
        requester_id=user.id,
        raw_text=payload.raw_text,
        resolved=resolved,
        section_ids=section_ids,
        now=datetime.now(UTC),
    )
    return _out(request, items)


@router.get("/changes/{request_id}", response_model=ChangeCheckOut)
def get_change(request_id: uuid.UUID, user: CurrentUser, db: DbSession) -> ChangeCheckOut:
    found = change_service.get_for_owner(db, owner_id=user.id, request_id=request_id)
    if found is None:
        raise _not_found()
    request, items = found
    return _out(request, items)
