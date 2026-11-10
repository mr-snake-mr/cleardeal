"""Payment routes. docs/08 §5, and no second path that marks a payment paid.

`paypal_client_for` is a module-level seam: production builds a real client from
the merchant's decrypted credentials, tests replace the builder. That is the only
place the two worlds meet, so the route logic has one shape in both.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.config import settings
from app.crypto import CredentialUnreadable
from app.database import get_db
from app.links import base_url
from app.routers.auth import CurrentUser
from app.schemas.payments import CaptureRequest, PaymentCreatedOut, PaymentOut
from app.services import merchants as merchant_service
from app.services import payments as payment_service
from app.services.paypal import (
    PayPalClient,
    PayPalCredentials,
    PayPalRejected,
    PayPalUnavailable,
)

router = APIRouter(tags=["payments"])

DbSession = Annotated[Session, Depends(get_db)]


def paypal_client_for(credentials: tuple[str, str]) -> PayPalClient:
    """Built per request: the token cache then lives as long as the request,
    which is correct — the merchant is per-tenant and nothing else may reuse it."""
    client_id, client_secret = credentials
    return PayPalClient(
        PayPalCredentials(client_id=client_id, client_secret=client_secret),
        env=settings.paypal_env,
    )


def _not_found(what: str = "Payment") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "NOT_FOUND", "message": f"{what} not found."},
    )


def _refusal(exc: payment_service.PaymentError) -> HTTPException:
    if isinstance(exc, payment_service.NoMerchant):
        code, message = "MERCHANT_NOT_CONNECTED", "Connect a PayPal merchant account first."
    elif isinstance(exc, payment_service.ActivePaymentExists):
        code, message = "PAYMENT_ALREADY_ACTIVE", "This milestone already has a live payment."
    elif isinstance(exc, payment_service.AgreementNotApproved):
        code, message = (
            "AGREEMENT_NOT_APPROVED",
            f"The agreement version is {exc.status}. Money follows the approved scope.",
        )
    elif isinstance(exc, payment_service.PaymentStateInvalid):
        code, message = "PAYMENT_STATE_INVALID", f"This payment is {exc.status}."
    elif isinstance(exc, payment_service.OrderMismatch):
        code, message = "ORDER_MISMATCH", "That order id does not belong to this payment."
    else:
        code, message = (
            "RECONCILIATION_REQUIRED",
            "PayPal's response did not prove the capture. Nothing was marked paid; "
            "check the order in PayPal before trying again.",
        )
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT, detail={"code": code, "message": message}
    )


def _provider_failure(exc: Exception) -> HTTPException:
    if isinstance(exc, PayPalUnavailable):
        detail = (
            "PayPal did not respond. The payment is unchanged; retry is safe."
            if not exc.attempted
            else "PayPal did not confirm. The order may already be captured — do not retry; "
            "check the order in PayPal."
        )
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "PAYPAL_UNAVAILABLE", "message": detail},
        )
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail={
            "code": "PAYPAL_REJECTED",
            "message": "PayPal rejected the request. Check the connected credentials.",
        },
    )


def _credentials(db: Session, user_id: uuid.UUID) -> tuple[str, str]:
    try:
        credentials = merchant_service.credentials_for(db, user_id=user_id)
    except CredentialUnreadable:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "MERCHANT_CREDENTIALS_UNREADABLE",
                "message": "The stored credentials cannot be decrypted. Reconnect the account.",
            },
        ) from None
    if credentials is None:
        raise _refusal(payment_service.NoMerchant())
    return credentials


@router.post(
    "/milestones/{milestone_id}/payment",
    response_model=PaymentCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
def create_payment(
    milestone_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
    db: DbSession,
    response: Response,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=8, max_length=80)
    ],
) -> PaymentCreatedOut:
    context = payment_service.milestone_context(
        db, owner_id=user.id, milestone_id=milestone_id
    )
    if context is None:
        raise _not_found("Milestone")
    milestone, version, project = context

    credentials = _credentials(db, user.id)
    client = paypal_client_for(credentials)
    base = base_url(request)

    try:
        payment, replayed, approve_url = payment_service.create(
            db,
            milestone=milestone,
            version=version,
            project=project,
            owner_id=user.id,
            idempotency_key=idempotency_key,
            client=client,
            return_url=f"{base}/payments/return",
            cancel_url=f"{base}/payments/cancel",
        )
    except payment_service.PaymentError as exc:
        raise _refusal(exc) from None
    except (PayPalUnavailable, PayPalRejected) as exc:
        raise _provider_failure(exc) from None

    if replayed:
        # The same key returns the existing payment, and 200 rather than 201:
        # nothing was created this time.
        response.status_code = status.HTTP_200_OK

    return PaymentCreatedOut(
        id=payment.id,
        payment_id=payment.id,
        project_id=payment.project_id,
        milestone_id=payment.milestone_id,
        amount=payment.amount,
        currency=payment.currency,
        status=payment.status,
        paypal_order_id=payment.paypal_order_id,
        paypal_capture_id=payment.paypal_capture_id,
        captured_at=payment.captured_at,
        paypal_client_id=credentials[0],
        # PayPal issues the approve link once, with the order. On a replay the
        # caller rebuilds it from the stored order id.
        approve_url=approve_url
        or (
            f"https://www.{'sandbox.' if settings.paypal_env == 'sandbox' else ''}"
            f"paypal.com/checkoutnow?token={payment.paypal_order_id}"
        ),
    )


@router.get("/payments/{payment_id}", response_model=PaymentOut)
def get_payment(payment_id: uuid.UUID, user: CurrentUser, db: DbSession) -> PaymentOut:
    payment = payment_service.get_for_owner(db, owner_id=user.id, payment_id=payment_id)
    if payment is None:
        raise _not_found()
    return PaymentOut.model_validate(payment)


@router.post("/payments/{payment_id}/capture", response_model=PaymentOut)
def capture_payment(
    payment_id: uuid.UUID, payload: CaptureRequest, user: CurrentUser, db: DbSession
) -> PaymentOut:
    payment = payment_service.get_for_owner(db, owner_id=user.id, payment_id=payment_id)
    if payment is None:
        raise _not_found()

    credentials = _credentials(db, user.id)
    client = paypal_client_for(credentials)

    try:
        captured = payment_service.capture(
            db,
            payment=payment,
            actor_id=user.id,
            order_id=payload.paypal_order_id,
            client=client,
            now=datetime.now(UTC),
        )
    except payment_service.PaymentError as exc:
        raise _refusal(exc) from None
    except (PayPalUnavailable, PayPalRejected) as exc:
        raise _provider_failure(exc) from None

    return PaymentOut.model_validate(captured)
