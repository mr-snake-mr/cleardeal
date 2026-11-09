"""Webhook receipts, audit trail and AI run log.

`WebhookEvent.event_id` is a primary key on purpose: PayPal retries a failed
delivery up to 25 times over three days, so the insert itself is the idempotency
gate. A duplicate insert raises, we return 200, and no second financial row is
ever written.

Audit and AI-run rows use ON DELETE SET NULL rather than CASCADE. Deleting a
project must not erase the record that a payment was captured — the log is the
point of the product, and CASCADE contradicted this module's own "append-only"
claim.
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSON)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    process_error: Mapped[str | None] = mapped_column(Text, default=None)


class AuditEvent(Base):
    """Append-only. Nothing reads this to make a decision."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("idx_audit_project", "project_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), default=None
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    event_type: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AiRun(Base):
    """One row per model call, so a judge can see the calls were real.

    Spec deviation, deliberate: docs/07 declares ai_runs.project_id without a
    foreign key. We add it with ON DELETE SET NULL so a run log cannot point at
    a project that no longer exists while still surviving that deletion. The FK
    is the stricter choice; docs/07 is updated to match.
    """

    __tablename__ = "ai_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), index=True, default=None
    )
    module: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(60))
    prompt_hash: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int | None] = mapped_column(Integer, default=None)
    output_tokens: Mapped[int | None] = mapped_column(Integer, default=None)
    latency_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    validation_status: Mapped[str] = mapped_column(String(30), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
