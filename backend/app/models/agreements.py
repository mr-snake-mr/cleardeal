"""Agreements, versions, sections, milestones, change requests.

`agreement_sections` is why this product exists: stable section ids are what let
the AI cite evidence and let the service verify that citation. Without them the
scope comparison cannot be checked, only believed.

UUID primary keys: postgres-patterns recommends bigint for index locality. We
keep UUID v4 because project and version ids travel in client-shareable review
links, where a sequential id is enumerable.

Evidence rule: "a non-NEEDS_CLARIFICATION item must carry evidence" is enforced
in the service layer, NOT as a CHECK constraint. `json_array_length` behaves
differently across SQLite and Postgres for non-array values, and a
dialect-dependent constraint is worse than an explicit, tested service rule.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.money import Money


class Agreement(Base):
    __tablename__ = "agreements"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), unique=True
    )
    current_version: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))


class AgreementVersion(Base):
    __tablename__ = "agreement_versions"
    __table_args__ = (
        UniqueConstraint("agreement_id", "version_no", name="uq_version_number"),
        CheckConstraint("budget_amount > 0", name="ck_version_budget_positive"),
        CheckConstraint(
            "status IN ('DRAFT','PENDING_CLIENT','APPROVED','SUPERSEDED','CANCELLED')",
            name="ck_version_status",
        ),
        CheckConstraint("version_no >= 1", name="ck_version_no_positive"),
        Index("idx_versions_agreement", "agreement_id", "version_no"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    agreement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agreements.id", ondelete="CASCADE"))
    version_no: Mapped[int] = mapped_column(Integer)
    # server_default, not only a Python default: docs/07 declares the DEFAULT in
    # the DDL, and a raw INSERT (migration script, psql, a future job) must not
    # be able to create a row with a NULL status.
    status: Mapped[str] = mapped_column(
        String(20), default="DRAFT", server_default=text("'DRAFT'")
    )
    budget_amount: Mapped[Decimal] = mapped_column(Money)
    # A version cannot outlive its author.
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    freelancer_approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    client_approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    # sha256 over the rendered sections + milestones at approval time.
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgreementSection(Base):
    __tablename__ = "agreement_sections"
    __table_args__ = (UniqueConstraint("version_id", "section_key", name="uq_section_key"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agreement_versions.id", ondelete="CASCADE")
    )
    section_key: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    ordinal: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))


class Milestone(Base):
    __tablename__ = "milestones"
    __table_args__ = (
        UniqueConstraint("version_id", "ordinal", name="uq_milestone_ordinal"),
        CheckConstraint("amount > 0", name="ck_milestone_amount_positive"),
        CheckConstraint(
            "status IN ('PLANNED','REQUESTED','PAID','CANCELLED')", name="ck_milestone_status"
        ),
        # docs/07 §4. uq_milestone_ordinal covers the same prefix, but the spec
        # names this index explicitly and a reader should not have to infer it.
        Index("idx_milestones_version", "version_id", "ordinal"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agreement_versions.id", ondelete="CASCADE")
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    deliverables: Mapped[list] = mapped_column(JSON)
    acceptance: Mapped[list] = mapped_column(JSON)
    amount: Mapped[Decimal] = mapped_column(Money)
    due_date: Mapped[date | None] = mapped_column(Date, default=None)
    status: Mapped[str] = mapped_column(
        String(20), default="PLANNED", server_default=text("'PLANNED'")
    )


class ChangeRequest(Base):
    __tablename__ = "change_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ANALYSING','PROPOSED','SENT','APPROVED','REJECTED','WITHDRAWN')",
            name="ck_change_status",
        ),
        Index("idx_changes_project", "project_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    # The exact version the comparison was made against — the core guarantee is
    # that this pointer never moves.
    base_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agreement_versions.id", ondelete="CASCADE")
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    # ANALYSING-stage rows may exist with zero items and zero evidence; the
    # evidence rule applies only once a proposal is finalised. The service owns
    # that rule; the database deliberately does not enforce it mid-flight.
    raw_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(20), default="ANALYSING", server_default=text("'ANALYSING'")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChangeItem(Base):
    __tablename__ = "change_items"
    __table_args__ = (
        CheckConstraint(
            "classification IN ('ALREADY_INCLUDED','OUT_OF_SCOPE','NEEDS_CLARIFICATION')",
            name="ck_change_class",
        ),
        CheckConstraint("confidence IN ('high','medium','low')", name="ck_change_confidence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("change_requests.id", ondelete="CASCADE")
    )
    feature: Mapped[str] = mapped_column(String(300))
    classification: Mapped[str] = mapped_column(String(30))
    explanation: Mapped[str] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(String(10))
    # Only ever written after the service has validated each citation.
    evidence: Mapped[list] = mapped_column(JSON, default=list, server_default=text("'[]'"))
    effort_low_h: Mapped[int | None] = mapped_column(Integer, default=None)
    effort_high_h: Mapped[int | None] = mapped_column(Integer, default=None)
    # Nullable on purpose: an ALREADY_INCLUDED item must never carry a price.
    suggested_cost: Mapped[Decimal | None] = mapped_column(Money, default=None)
    timeline_days: Mapped[int | None] = mapped_column(Integer, default=None)
