"""Project routes. Parse, authorise, call a service, serialise.

`CurrentUser` comes from `routers.auth`, so the only way to reach these routes
is a session whose id maps to a live user row. There is no second auth path.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.ai.client import AiInvalidOutput, AiInvalidRequest, AiUnavailable
from app.ai.client import get_client as get_ai_client
from app.ai.schemas import BriefAnalysis
from app.database import get_db
from app.routers.auth import CurrentUser
from app.schemas.projects import ProjectCreate, ProjectList, ProjectOut, ProjectUpdate
from app.services import analysis as analysis_service
from app.services import projects as project_service
from app.services.ai_runs import make_recorder

router = APIRouter(prefix="/projects", tags=["projects"])

DbSession = Annotated[Session, Depends(get_db)]


def _not_found() -> HTTPException:
    # 404 for a project that exists but belongs to someone else, as well: 403
    # would confirm the id, and the ids are UUID v4 precisely so they are not
    # enumerable (docs/10 §2).
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "NOT_FOUND", "message": "Project not found."},
    )


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, user: CurrentUser, db: DbSession) -> ProjectOut:
    if payload.deadline is not None and payload.deadline < date.today():
        raise HTTPException(
            status_code=422,  # UNPROCESSABLE_CONTENT: the constant name moved in starlette 1.x
            detail={
                "code": "VALIDATION_ERROR",
                "message": "deadline must not be in the past",
                "details": {"fields": {"deadline": "must be today or later"}},
            },
        )
    project = project_service.create(db, owner_id=user.id, payload=payload)
    return ProjectOut.model_validate(project)


@router.get("", response_model=ProjectList)
def list_projects(user: CurrentUser, db: DbSession) -> ProjectList:
    rows, total = project_service.list_for_owner(db, owner_id=user.id)
    return ProjectList(items=[ProjectOut.model_validate(p) for p in rows], total=total)


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: uuid.UUID, user: CurrentUser, db: DbSession) -> ProjectOut:
    project = project_service.get_for_owner(db, owner_id=user.id, project_id=project_id)
    if project is None:
        raise _not_found()
    return ProjectOut.model_validate(project)

@router.post("/{project_id}/analyse", response_model=BriefAnalysis)
def analyse_project(project_id: uuid.UUID, user: CurrentUser, db: DbSession) -> BriefAnalysis:
    """M1. The project moves to ANALYSED only when the analysis succeeded.

    Every failure path leaves the project exactly where it was: a half-analysed
    project is worse than an unanalysed one, because it looks finished.
    """
    project = project_service.get_for_owner(db, owner_id=user.id, project_id=project_id)
    if project is None:
        raise _not_found()

    # Only a DRAFT can be analysed. Without this, re-analysing an APPROVED or
    # COMPLETED project silently rewinds it, and nothing in the schema objects.
    if project.status != "DRAFT":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "PROJECT_NOT_DRAFT",
                "message": f"Only a draft can be analysed; this project is {project.status}.",
            },
        )

    client = get_ai_client(record=make_recorder(db))
    try:
        return analysis_service.analyse(db, project=project, client=client)
    except AiUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "AI_UNAVAILABLE",
                "message": "The analysis service is unavailable. The project was not changed.",
            },
        ) from None
    except AiInvalidRequest as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "AI_INVALID_REQUEST",
                # The reason travels: "rejected the request (403)" is the
                # difference between a wrong key and a rate limit, and the UI
                # already prints `message`. It carries no key and no URL — the
                # client raises `from None` precisely so neither leaks.
                "message": f"The analysis service rejected the request. ({exc})",
            },
        ) from None
    except AiInvalidOutput:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "AI_INVALID_OUTPUT",
                "message": "The analysis could not be trusted and was discarded. Try again.",
            },
        ) from None

@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(
    project_id: uuid.UUID, payload: ProjectUpdate, user: CurrentUser, db: DbSession
) -> ProjectOut:
    project = project_service.get_for_owner(db, owner_id=user.id, project_id=project_id)
    if project is None:
        raise _not_found()
    return ProjectOut.model_validate(
        project_service.update(db, project=project, changes=payload)
    )


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    project = project_service.get_for_owner(db, owner_id=user.id, project_id=project_id)
    if project is None:
        raise _not_found()
    try:
        project_service.delete(db, project=project, actor_id=user.id)
    except project_service.ProjectHasApprovedAgreement:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "PROJECT_HAS_APPROVED_AGREEMENT",
                "message": (
                    "This project has a signed agreement. Deleting it would destroy the "
                    "hash and both signatures, which are the evidence it was signed."
                ),
            },
        ) from None
    except project_service.ProjectHasPayments:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "PROJECT_HAS_PAYMENTS",
                "message": (
                    "This project has payments against it and cannot be deleted. "
                    "A payment record is evidence; it does not disappear because the "
                    "project did."
                ),
            },
        ) from None
