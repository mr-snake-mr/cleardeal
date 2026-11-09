"""Payments: creation and the one route that may mark money received.

Two rules, both from docs/08 §5:

- **Creation reserves the idempotency key before it touches PayPal.** The row is
  inserted first and committed only after the order exists, so a double-click
  loses at the UNIQUE constraint instead of buying the work twice.
- **A capture that cannot prove itself is never recorded as paid.** The refusal
  leaves `captured_at` NULL and the payment where it was; the webhook is what
  converges a capture whose database write failed.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Agreement, AgreementVersion, AuditEvent, Milestone, Payment, Project
from app.services.paypal import PayPalClient, PayPalVerificationError


class PaymentError(Exception):
    """Base for refusals the router turns into specific 409s."""


class NoMerchant(PaymentError):
    def __init__(self) -> None:
        super().__init__("no PayPal merchant is connected")


class ActivePaymentExists(PaymentError):
    def __init__(self) -> None:
        super().__init__("this milestone already has a live payment")


class AgreementNotApproved(PaymentError):
    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(f"the agreement version is {status}, not APPROVED")


class PaymentStateInvalid(PaymentError):
    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(f"payment is {status}")


class OrderMismatch(PaymentError):
    def __init__(self) -> None:
        super().__init__("the order id does not match the one stored for this payment")


class ReconciliationRequired(PaymentError):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)


def milestone_context(
    db: Session, *, owner_id: uuid.UUID, milestone_id: uuid.UUID
) -> tuple[Milestone, AgreementVersion, Project] | None:
    """Scoped in SQL, like every other owner query: a non-owner gets `None`."""
    row = db.execute(
        select(Milestone, AgreementVersion, Project)
        .join(AgreementVersion, Milestone.version_id == AgreementVersion.id)
        .join(Agreement, AgreementVersion.agreement_id == Agreement.id)
        .join(Project, Agreement.project_id == Project.id)
        .where(Milestone.id == milestone_id, Project.owner_id == owner_id)
    ).one_or_none()
    return (row[0], row[1], row[2]) if row else None


def get_for_owner(
    db: Session, *, owner_id: uuid.UUID, payment_id: uuid.UUID
) -> Payment | None:
    return db.execute(
        select(Payment)
        .join(Project, Payment.project_id == Project.id)
        .where(Payment.id == payment_id, Project.owner_id == owner_id)
    ).scalar_one_or_none()


def create(
    db: Session,
    *,
    milestone: Milestone,
    version: AgreementVersion,
    project: Project,
    owner_id: uuid.UUID,
    idempotency_key: str,
    client: PayPalClient,
    return_url: str,
    cancel_url: str,
) -> tuple[Payment, bool, str | None]:
    """Returns (payment, replayed, approve_url).

    `approve_url` is only known on a fresh order; on a replay the caller
    rebuilds it from the stored order id, because PayPal issues it once.
    """
    replay = db.scalars(
        select(Payment).where(Payment.idempotency_key == idempotency_key)
    ).one_or_none()
    if replay is not None:
        return replay, True, None

    if version.status != "APPROVED":
        # Money follows the approved scope. Paying a draft would make the
        # product's central claim decoration.
        raise AgreementNotApproved(version.status)

    active = db.scalars(
        select(Payment).where(
            Payment.milestone_id == milestone.id,
            Payment.status.in_(("CREATED", "APPROVED")),
        )
    ).one_or_none()
    if active is not None:
        raise ActivePaymentExists()

    payment = Payment(
        project_id=project.id,
        milestone_id=milestone.id,
        # Read inside the transaction, from the row — never from the request.
        amount=milestone.amount,
        currency=project.currency,
        status="CREATED",
        merchant_user_id=owner_id,
        idempotency_key=idempotency_key,
    )
    db.add(payment)
    try:
        # The key is reserved before PayPal is called: a double-click loses here
        # rather than at the provider.
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ActivePaymentExists() from exc

    order = client.create_order(
        amount=Decimal(payment.amount),
        currency=payment.currency,
        reference_id=str(milestone.id),
        custom_id=f"{project.id}:{milestone.id}:{version.version_no}",
        return_url=return_url,
        cancel_url=cancel_url,
    )
    payment.paypal_order_id = order.order_id
    db.add(
        AuditEvent(
            project_id=project.id,
            actor_id=owner_id,
            event_type="PAYMENT_ORDER_CREATED",
            payload={
                "payment_id": str(payment.id),
                "milestone_id": str(milestone.id),
                "amount": f"{payment.amount:.2f}",
                "order_id": order.order_id,
            },
        )
    )
    db.commit()
    db.refresh(payment)
    return payment, False, order.approve_url


def capture(
    db: Session,
    *,
    payment: Payment,
    actor_id: uuid.UUID,
    order_id: str,
    client: PayPalClient,
    now: datetime,
) -> Payment:
    if payment.status not in ("CREATED", "APPROVED"):
        raise PaymentStateInvalid(payment.status)
    if order_id != payment.paypal_order_id:
        # Checked before any provider call: capturing someone else's order is
        # the one mistake this route must never make.
        raise OrderMismatch()

    try:
        result = client.capture(
            order_id=order_id,
            expected_amount=Decimal(payment.amount),
            expected_currency=payment.currency,
        )
    except PayPalVerificationError as exc:
        # The provider may have moved money; our record must not claim it did.
        db.rollback()
        raise ReconciliationRequired(str(exc)) from exc

    payment.status = "CAPTURED"
    payment.paypal_capture_id = result.capture_id
    payment.captured_at = now

    milestone = db.get(Milestone, payment.milestone_id)
    if milestone is not None:
        milestone.status = "PAID"

    db.add(
        AuditEvent(
            project_id=payment.project_id,
            actor_id=actor_id,
            event_type="PAYMENT_CAPTURED",
            payload={
                "payment_id": str(payment.id),
                "capture_id": result.capture_id,
                "amount": f"{payment.amount:.2f}",
                # A-2: record absence rather than inventing an identifier.
                "payee_merchant_id": result.payee_merchant_id,
                "payee_present": result.payee_merchant_id is not None,
            },
        )
    )
    db.commit()
    db.refresh(payment)
    return payment
