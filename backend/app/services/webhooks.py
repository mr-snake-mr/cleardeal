"""Webhook verification and reconciliation.

The order is the security property (docs/04 §4.3, docs/08 §8):

1. The signature is checked over the **raw** body. Re-serialising a parsed body
   produces different bytes and a different CRC32, which is how a correct event
   gets rejected — or worse, how a tampered one gets accepted if the check is
   moved after parsing.
2. The event id is the idempotency gate: PayPal retries 25 times over three
   days, so a duplicate must return 200 having written nothing.
3. Reconciliation never invents a payment. An event for an order we never
   created is recorded and ignored; it is not an error, because a 400 would make
   PayPal retry for three days.

Known limitation, stated rather than hidden: the certificate is fetched from the
URL PayPal supplies and trusted, which is the "self-verify" method the docs
prefer. A stricter build would pin PayPal's CA or use `verify-webhook-signature`.
"""

from __future__ import annotations

import base64
import json
import uuid
import zlib
from datetime import datetime

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AuditEvent, Milestone, Payment, WebhookEvent


class SignatureInvalid(Exception):
    """The transmission could not be trusted. Nothing may change."""


HEADERS = {
    "id": "paypal-transmission-id",
    "time": "paypal-transmission-time",
    "sig": "paypal-transmission-sig",
}


def signed_string(transmission_id: str, timestamp: str, webhook_id: str, raw: bytes) -> str:
    """`transmissionId|timeStamp|webhookId|crc32`, per docs/04 §1.2.

    zlib's CRC32 is signed in Python; PayPal sends the unsigned value.
    """
    return f"{transmission_id}|{timestamp}|{webhook_id}|{zlib.crc32(raw) & 0xFFFFFFFF}"


# PayPal's self-verify scheme signs a CRC32 of the body, not the body: an
# observed transmission can be repacked with a different body and the same
# checksum. Freshness is the cheap half of the mitigation — a captured
# transmission stops working. The complete answer is the `verify-webhook-signature`
# postback, recorded in docs/16 as the production path.
_MAX_TRANSMISSION_AGE_SECONDS = 300


def _is_fresh(stamp: str, now: datetime) -> bool:
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        return False
    age = (now - parsed).total_seconds()
    return -60 <= age <= _MAX_TRANSMISSION_AGE_SECONDS


def verify(*, raw: bytes, headers, webhook_id: str, cert_pem: bytes, now=None) -> str:
    """Raises `SignatureInvalid`; returns the transmission id for the log line."""
    # `headers` arrives keyed by role ("id"/"time"/"sig"); the HTTP names live in
    # HEADERS so the router has one place to read them from the request.
    values = {key: headers.get(key) for key in HEADERS}
    if not webhook_id or not all(values.values()):
        raise SignatureInvalid("missing transmission headers or webhook id")

    try:
        signature = base64.b64decode(values["sig"], validate=True)
    except (ValueError, TypeError) as exc:
        raise SignatureInvalid("signature was not base64") from exc

    try:
        certificate = x509.load_pem_x509_certificate(cert_pem)
        certificate.public_key().verify(
            signature,
            signed_string(values["id"], values["time"], webhook_id, raw).encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise SignatureInvalid("signature did not verify") from exc

    if now is not None and not _is_fresh(values["time"], now):
        raise SignatureInvalid("transmission timestamp is outside the window")

    return values["id"]


def handle(db: Session, *, event: dict, now: datetime) -> str:
    """Insert first (the gate), then reconcile. Returns an outcome word."""
    event_id = event.get("id")
    event_type = event.get("event_type")
    if not isinstance(event_id, str) or not isinstance(event_type, str):
        raise ValueError("event is missing id or event_type")

    if db.get(WebhookEvent, event_id) is not None:
        return "duplicate"

    row = WebhookEvent(
        event_id=event_id, event_type=event_type, payload=event, verified_at=now
    )
    db.add(row)
    try:
        # Two deliveries of the same event can race; the primary key decides.
        db.flush()
    except IntegrityError:
        db.rollback()
        return "duplicate"

    outcome = _reconcile(db, event_type, event.get("resource") or {}, now)
    row.processed_at = now
    db.commit()
    return outcome


def _reconcile(db: Session, event_type: str, resource: dict, now: datetime) -> str:
    order_id = (
        (resource.get("supplementary_data") or {}).get("related_ids", {}).get("order_id")
        if isinstance(resource.get("supplementary_data"), dict)
        else None
    )
    if not order_id:
        return "unmatched"

    payment = db.scalars(
        select(Payment).where(Payment.paypal_order_id == str(order_id))
    ).one_or_none()
    if payment is None:
        return "unmatched"

    if event_type == "CHECKOUT.ORDER.APPROVED":
        if payment.status == "CREATED":
            payment.status = "APPROVED"
        return "approved"

    if event_type == "PAYMENT.CAPTURE.DENIED":
        if payment.status in ("CREATED", "APPROVED"):
            payment.status = "FAILED"
        return "denied"

    if event_type == "PAYMENT.CAPTURE.REFUNDED":
        payment.status = "REFUNDED"
        return "refunded"

    if event_type != "PAYMENT.CAPTURE.COMPLETED":
        return "ignored"

    if payment.status == "CAPTURED":
        # Already booked from the capture response. Converging twice is a no-op,
        # never a second audit row claiming another payment.
        return "already"

    capture_id = resource.get("id")
    if not isinstance(capture_id, str) or not capture_id:
        return "unmatched"

    payment.status = "CAPTURED"
    payment.paypal_capture_id = capture_id
    payment.captured_at = now

    milestone = db.get(Milestone, payment.milestone_id)
    if milestone is not None:
        milestone.status = "PAID"

    payee = resource.get("payee") if isinstance(resource.get("payee"), dict) else {}
    db.add(
        AuditEvent(
            project_id=payment.project_id,
            actor_id=None,  # PayPal is the actor; there is no user session here.
            event_type="PAYMENT_CAPTURED_BY_WEBHOOK",
            payload={
                "payment_id": str(payment.id),
                "capture_id": capture_id,
                "amount": f"{payment.amount:.2f}",
                "payee_merchant_id": payee.get("merchant_id"),
                "payee_present": bool(payee.get("merchant_id")),
            },
        )
    )
    return "reconciled"


def load_event(raw: bytes) -> dict:
    try:
        event = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("body was not JSON") from exc
    if not isinstance(event, dict):
        raise ValueError("body was not a JSON object")
    return event


def payment_id_or_none(db: Session, order_id: str) -> uuid.UUID | None:
    payment = db.scalars(select(Payment).where(Payment.paypal_order_id == order_id)).one_or_none()
    return payment.id if payment else None
