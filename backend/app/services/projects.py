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

from app.models import AuditEvent, Project
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
    # Same transaction as the write, so the log cannot outlive a rollback and
    # cannot be skipped by a caller that forgets (docs/08 §1).
    db.flush()
    db.add(
        AuditEvent(
            project_id=project.id,
            actor_id=owner_id,
            event_type="PROJECT_CREATED",
            payload={"name": project.name, "budget_amount": f"{payload.budget_amount:.2f}"},
        )
    )
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


class ProjectHasApprovedAgreement(Exception):
    """A signed agreement is evidence too.

    Deleting the project destroys the hash, both approval timestamps and the
    milestone amounts, while the approval audit rows survive pointing at nothing.
    """


class ProjectHasPayments(Exception):
    """A project with money attached is not deletable.

    The foreign keys cascade from project to agreement to milestone, but
    `payments` is RESTRICT, so the database would refuse anyway. This turns that
    into a 409 that names the reason.
    """


def update(db: Session, *, project: Project, changes) -> Project:
    """Apply only the fields the caller actually sent."""
    for field, value in changes.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    db.commit()
    db.refresh(project)
    return project


def delete(db: Session, *, project: Project, actor_id: uuid.UUID) -> None:
    from sqlalchemy import func, select

    from app.models import Agreement, AgreementVersion, AuditEvent, Payment

    project_id = project.id
    held = db.scalar(
        select(func.count()).select_from(Payment).where(Payment.project_id == project_id)
    )
    if held:
        raise ProjectHasPayments()

    signed = db.scalar(
        select(func.count())
        .select_from(AgreementVersion)
        .join(Agreement, AgreementVersion.agreement_id == Agreement.id)
        .where(Agreement.project_id == project_id, AgreementVersion.status == "APPROVED")
    )
    if signed:
        raise ProjectHasApprovedAgreement()

    # Same transaction as the delete: an audit row written after a commit is an
    # audit row that can be lost, leaving a deletion nobody can attribute.
    db.add(
        AuditEvent(
            project_id=None,
            actor_id=actor_id,
            event_type="PROJECT_DELETED",
            payload={"project_id": str(project_id)},
        )
    )
    db.delete(project)
    db.commit()
