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

from app.database import get_db
from app.routers.auth import CurrentUser
from app.schemas.projects import ProjectCreate, ProjectList, ProjectOut
from app.services import projects as project_service

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
