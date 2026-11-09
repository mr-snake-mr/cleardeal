"""The webhook: the only endpoint an attacker can reach without a session.

docs/08 §8 and docs/04 §4.3, in order: raw bytes before parsing, verify the
signature over that raw body, insert the event id as the idempotency gate, then
reconcile. A `200` before verification would make this a way to mark invoices
paid, so every test below that returns 400 asserts **zero** state change.
"""

from __future__ import annotations

import base64
import json
import uuid
import zlib
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID
from sqlalchemy import delete, func, select

from app.database import SessionLocal
from app.models import (
    Agreement,
    AgreementVersion,
    AuditEvent,
    MerchantAccount,
    Milestone,
    Payment,
    Project,
    User,
    WebhookEvent,
)

WEBHOOK_ID = "WH-TEST-0001"


@pytest.fixture(scope="module")
def signing():
    """A key and a certificate that stand in for PayPal's."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "paypal.test")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime(2026, 1, 1, tzinfo=UTC))
        .not_valid_after(datetime(2027, 1, 1, tzinfo=UTC))
        .sign(key, hashes.SHA256())
    )
    return key, cert.public_bytes(serialization.Encoding.PEM)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _clean():
    session = SessionLocal()
    try:
        for model in (
            AuditEvent,
            WebhookEvent,
            Payment,
            Milestone,
            AgreementVersion,
            Agreement,
            MerchantAccount,
            Project,
            User,
        ):
            session.execute(delete(model))
        session.commit()
    finally:
        session.close()
    yield


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "paypal_webhook_id", WEBHOOK_ID, raising=False)


@pytest.fixture(autouse=True)
def _cert(monkeypatch, signing):
    """The cert fetch is a seam; the signature check is not."""
    _, pem = signing
    from app.routers import webhooks as router_module

    monkeypatch.setattr(router_module, "fetch_certificate", lambda url: pem)


def _headers(signing, raw: bytes, *, transmission="tid-0001", webhook_id=WEBHOOK_ID) -> dict:
    key, _ = signing
    stamp = "2026-11-09T12:00:00Z"
    crc = zlib.crc32(raw) & 0xFFFFFFFF
    signed = f"{transmission}|{stamp}|{webhook_id}|{crc}"
    signature = base64.b64encode(
        key.sign(signed.encode(), padding.PKCS1v15(), hashes.SHA256())
    ).decode()
    return {
        "paypal-transmission-id": transmission,
        "paypal-transmission-time": stamp,
        "paypal-transmission-sig": signature,
        "paypal-cert-url": "https://api.paypal.com/cert.pem",
    }


def _capture_event(*, order_id: str, capture_id="3C679366HH908993F", event_id="WH-EVT-1") -> dict:
    return {
        "id": event_id,
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {
            "id": capture_id,
            "status": "COMPLETED",
            "amount": {"currency_code": "USD", "value": "120.00"},
            "supplementary_data": {"related_ids": {"order_id": order_id}},
        },
    }


def _payment(db, *, status="CREATED", order_id="5O190127TN364715T") -> Payment:
    """The smallest consistent payment row: owner → project → version → milestone."""
    user = User(email=f"{uuid.uuid4().hex}@example.test", name="Sam")
    db.add(user)
    db.flush()
    project = Project(
        owner_id=user.id,
        name="Marketing site",
        client_email="client@example.test",
        brief="A five page marketing site with a blog and a contact form.",
        budget_amount=Decimal("300.00"),
    )
    db.add(project)
    db.flush()
    agreement = Agreement(project_id=project.id, current_version=1)
    db.add(agreement)
    db.flush()
    version = AgreementVersion(
        agreement_id=agreement.id,
        version_no=1,
        status="APPROVED",
        budget_amount=Decimal("300.00"),
        created_by=user.id,
        content_hash="0" * 64,
    )
    db.add(version)
    db.flush()
    milestone = Milestone(
        version_id=version.id,
        ordinal=1,
        title="Design",
        deliverables=["wireframes"],
        acceptance=["signed off"],
        amount=Decimal("120.00"),
    )
    db.add(milestone)
    db.flush()
    payment = Payment(
        project_id=project.id,
        milestone_id=milestone.id,
        amount=Decimal("120.00"),
        currency="USD",
        status=status,
        merchant_user_id=user.id,
        paypal_order_id=order_id,
        idempotency_key=uuid.uuid4().hex,
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return payment


def _post(client, signing, payload: dict, *, raw: bytes | None = None, headers: dict | None = None):
    body = raw if raw is not None else json.dumps(payload).encode()
    return client.post(
        "/webhooks/paypal",
        content=body,
        headers=headers if headers is not None else _headers(signing, body),
    )


# --------------------------------------------------------------------- happy path


def test_a_verified_capture_completes_the_payment(client, db, signing):
    payment = _payment(db)
    payload = _capture_event(order_id=payment.paypal_order_id)

    response = _post(client, signing, payload)

    assert response.status_code == 200, response.text
    db.expire_all()
    updated = db.get(Payment, payment.id)
    assert updated.status == "CAPTURED"
    assert updated.paypal_capture_id == "3C679366HH908993F"
    assert updated.captured_at is not None
    assert db.get(Milestone, payment.milestone_id).status == "PAID"

    event = db.get(WebhookEvent, "WH-EVT-1")
    assert event is not None
    assert event.processed_at is not None


def test_a_capture_event_that_arrives_before_the_capture_response_converges(client, db, signing):
    """docs/04 §4.2: the capture succeeded and our write failed. The webhook is
    the convergent path, so an event for a CREATED payment must complete it."""
    payment = _payment(db, status="CREATED")
    payload = _capture_event(order_id=payment.paypal_order_id, event_id="WH-EVT-EARLY")

    assert _post(client, signing, payload).status_code == 200

    db.expire_all()
    assert db.get(Payment, payment.id).status == "CAPTURED"


# ------------------------------------------------------------------ the two refusals


def test_a_replayed_event_id_writes_nothing_and_is_still_200(client, db, signing):
    """PayPal retries 25 times over three days. The gate is the primary key."""
    payment = _payment(db)
    payload = _capture_event(order_id=payment.paypal_order_id)

    assert _post(client, signing, payload).status_code == 200
    db.expire_all()
    first_count = db.scalar(select(func.count()).select_from(WebhookEvent))

    assert _post(client, signing, payload).status_code == 200
    db.expire_all()

    assert db.scalar(select(func.count()).select_from(WebhookEvent)) == first_count == 1


def test_a_tampered_body_is_refused_and_changes_nothing(client, db, signing):
    """The signature is over the raw bytes. Re-stringifying after a parse is the
    exact mistake docs/04 §1.2 warns about."""
    payment = _payment(db)
    payload = _capture_event(order_id=payment.paypal_order_id)
    original = json.dumps(payload).encode()
    headers = _headers(signing, original)
    altered = {**payload["resource"], "amount": {"currency_code": "USD", "value": "1.00"}}
    tampered = json.dumps({**payload, "resource": altered}).encode()

    response = client.post("/webhooks/paypal", content=tampered, headers=headers)

    assert response.status_code == 400
    db.expire_all()
    assert db.get(Payment, payment.id).status == "CREATED"
    assert db.scalar(select(func.count()).select_from(WebhookEvent)) == 0


def test_a_signature_over_a_different_webhook_id_is_refused(client, db, signing):
    payment = _payment(db)
    payload = _capture_event(order_id=payment.paypal_order_id)
    raw = json.dumps(payload).encode()
    headers = _headers(signing, raw, webhook_id="WH-SOMEONE-ELSE")

    assert client.post("/webhooks/paypal", content=raw, headers=headers).status_code == 400
    db.expire_all()
    assert db.get(Payment, payment.id).status == "CREATED"


def test_missing_signature_headers_are_refused(client, db):
    payload = _capture_event(order_id="5O190127TN364715T")

    assert client.post("/webhooks/paypal", content=json.dumps(payload).encode()).status_code == 400


def test_a_verified_but_unparseable_body_is_refused(client, db, signing):
    raw = b"not json at all"
    assert client.post(
        "/webhooks/paypal", content=raw, headers=_headers(signing, raw)
    ).status_code == 400


# ------------------------------------------------------------------- reconciliation


def test_an_event_for_an_unknown_order_is_recorded_and_ignored(client, db, signing):
    """A verified event we cannot reconcile is not an error — PayPal does not
    care, and a 400 would make it retry for three days."""
    payload = _capture_event(order_id="AN-ORDER-WE-NEVER-CREATED", event_id="WH-EVT-UNKNOWN")

    response = _post(client, signing, payload)

    assert response.status_code == 200
    db.expire_all()
    event = db.get(WebhookEvent, "WH-EVT-UNKNOWN")
    assert event is not None
    assert event.processed_at is not None
