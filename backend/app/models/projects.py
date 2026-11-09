"""Projects — the container everything else hangs from."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.money import Money


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        # Zero is not a valid budget — a free project is a different product.
        CheckConstraint("budget_amount > 0", name="ck_project_budget_positive"),
        CheckConstraint("currency IN ('USD')", name="ck_project_currency"),
        CheckConstraint(
            "status IN ('DRAFT','ANALYSED','AGREEMENT_DRAFT','AWAITING_CLIENT',"
            "'APPROVED','IN_PROGRESS','COMPLETED','CANCELLED')",
            name="ck_project_status",
        ),
        # Plain column order, not DESC: Alembic silently skips indexes that
        # contain an expression, so a desc() index would exist in the model and
        # never reach the database. Both supported dialects read a B-tree
        # backwards for ORDER BY ... DESC, so nothing is lost.
        Index("idx_projects_owner", "owner_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(200))
    client_email: Mapped[str] = mapped_column(String(320))
    brief: Mapped[str] = mapped_column(Text)
    budget_amount: Mapped[Decimal] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3), default="USD", server_default=text("'USD'"))
    deadline: Mapped[date | None] = mapped_column(Date, default=None)
    status: Mapped[str] = mapped_column(String(20), default="DRAFT", server_default=text("'DRAFT'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
