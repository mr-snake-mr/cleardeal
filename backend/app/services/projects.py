"""Project reads and writes.

Every query is scoped by `owner_id` in the WHERE clause, not by an `if` after
the fetch. Two consequences worth stating: a caller cannot forget the check,
and a non-owner gets `None` — which the router turns into `404`, because `403`
confirms the object exists and that is an enumeration oracle.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Project
from app.schemas.projects import ProjectCreate


def create(db: Session, *, owner_id: uuid.UUID, payload: ProjectCreate) -> Project:
    project = Project(
        owner_id=owner_id,
        name=payload.name,
        client_email=payload.client_email,
        brief=payload.brief,
        budget_amount=payload.budget_amount,
        deadline=payload.deadline,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def list_for_owner(db: Session, *, owner_id: uuid.UUID) -> tuple[list[Project], int]:
    """Newest first, with a total that counts every row the caller may see.

    No pagination yet: `total` is returned so the shape does not have to change
    when it arrives, and a demo with five projects does not need a cursor.
    """
    rows = (
        db.execute(
            select(Project)
            .where(Project.owner_id == owner_id)
            .order_by(Project.created_at.desc(), Project.id)
        )
        .scalars()
        .all()
    )
    total = db.execute(
        select(func.count()).select_from(Project).where(Project.owner_id == owner_id)
    ).scalar_one()
    return list(rows), int(total)


def get_for_owner(db: Session, *, owner_id: uuid.UUID, project_id: uuid.UUID) -> Project | None:
    return db.execute(
        select(Project).where(Project.id == project_id, Project.owner_id == owner_id)
    ).scalar_one_or_none()
