"""Payments and per-tenant merchant credentials.

Two UNIQUE constraints on the PayPal identifiers (order id, capture id) are the
structural defence against double-capture: a bug that tries to insert a second
row for the same capture fails at the database, not in review.

Deletion policy is deliberately asymmetric: anything touching money uses
RESTRICT. A project with payments cannot be deleted, because erasing a payment
record is how a dispute becomes unwinnable.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.money import Money


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_payment_amount_positive"),
        CheckConstraint("currency IN ('USD')", name="ck_payment_currency"),
        CheckConstraint(
            "status IN ('CREATED','APPROVED','CAPTURED','FAILED',"
            "'RECONCILIATION_REQUIRED','REFUNDED')",
            name="ck_payment_status",
        ),
        Index("idx_payments_milestone", "milestone_id"),
        # PP-15: at most one active payment per milestone. Declared here rather
        # than hand-patched into the migration, so the model stays the single
        # source of truth and autogenerate keeps DB and models in sync.
        Index(
            "uq_payments_active_per_milestone",
            "milestone_id",
            unique=True,
            sqlite_where=text("status IN ('CREATED','APPROVED')"),
            postgresql_where=text("status IN ('CREATED','APPROVED')"),
        ),
        # Dialect caveat: the two partial indexes carry sqlite_where and
        # postgresql_where only. On any other dialect Alembic would emit a FULL
        # unique index on milestone_id, i.e. one payment per milestone forever.
        # SQLite and Postgres are the only supported targets.
        # docs/07 §4: the "unpaid work" view never wants the captured rows.
        Index(
            "idx_payments_status",
            "status",
            sqlite_where=text("status <> 'CAPTURED'"),
            postgresql_where=text("status <> 'CAPTURED'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"))
    milestone_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("milestones.id", ondelete="RESTRICT")
    )
    amount: Mapped[Decimal] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3), default="USD", server_default=text("'USD'"))
    status: Mapped[str] = mapped_column(
        String(30), default="CREATED", server_default=text("'CREATED'")
    )
    merchant_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))

    paypal_order_id: Mapped[str | None] = mapped_column(String(64), unique=True, default=None)
    paypal_capture_id: Mapped[str | None] = mapped_column(String(64), unique=True, default=None)
    # Read from the verified capture response, never from the client.
    payee_merchant_id: Mapped[str | None] = mapped_column(String(64), default=None)

    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class MerchantAccount(Base):
    """Model B: a freelancer's own PayPal REST app credentials.

    Secrets are AES-GCM encrypted at rest with CREDENTIALS_ENCRYPTION_KEY and are
    never returned by any endpoint. Only used for order create / read / capture
    on behalf of the owning freelancer.

    The table exists even if Model A is chosen: the connect screen stays
    unmounted and no row is written. Dropping it would force a migration in the
    middle of Day 2 if the merchant model flips.
    """

    __tablename__ = "merchant_accounts"
    __table_args__ = (
        CheckConstraint("status IN ('CONNECTED','INVALID','REVOKED')", name="ck_merchant_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    encrypted_client_id: Mapped[bytes] = mapped_column(LargeBinary)
    encrypted_client_secret: Mapped[bytes] = mapped_column(LargeBinary)
    paypal_merchant_id: Mapped[str | None] = mapped_column(String(64), default=None)
    status: Mapped[str] = mapped_column(
        String(20), default="CONNECTED", server_default=text("'CONNECTED'")
    )
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
