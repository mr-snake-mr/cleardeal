"""Payments: the route that turns a milestone into money, and the one that turns
a PayPal response into `CAPTURED`.

Contract: docs/08 §5. The two behaviours worth defending are the idempotency key
(a double-click must not buy the work twice) and the reconciliation refusal (a
capture that does not prove itself leaves the payment un-paid, with
`captured_at` NULL and a `RECONCILIATION_REQUIRED` row, never a guess).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.ai.schemas import AgreementDraft
from app.database import SessionLocal
from app.models import (
    Agreement,
    AgreementSection,
    AgreementVersion,
    AuditEvent,
    MerchantAccount,
    Milestone,
    Payment,
    Project,
    User,
)
from app.services.paypal import CaptureResult, CreatedOrder, PayPalVerificationError

PASSWORD = "correct horse battery staple"

PROJECT = {
    "name": "Marketing site",
    "client_email": "client@example.test",
    "brief": "A five page marketing site with a blog and a contact form.",
    "budget_amount": "300.00",
}


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
            Payment,
            Milestone,
            AgreementSection,
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


class StubPayPal:
    """Records the calls; returns whatever the test scripted."""

    def __init__(self, *, created=None, captured=None, failure=None):
        self.created = created or CreatedOrder(
            order_id="5O190127TN364715T", approve_url="https://paypal.test/approve"
        )
        self.captured = captured or CaptureResult(
            capture_id="3C679366HH908993F", payee_merchant_id=None, status="COMPLETED"
        )
        self.failure = failure
        self.orders: list[dict] = []
        self.captures: list[dict] = []

    def create_order(self, **kwargs):
        self.orders.append(kwargs)
        return self.created

    def capture(self, **kwargs):
        self.captures.append(kwargs)
        if self.failure is not None:
            raise self.failure
        return self.captured


@pytest.fixture
def paypal(monkeypatch):
    """One seam: the router builds the client, the test replaces the builder."""

    def install(stub: StubPayPal) -> StubPayPal:
        from app.routers import payments as router_module

        monkeypatch.setattr(router_module, "paypal_client_for", lambda *a, **k: stub)
        return stub

    return install


def _signup(client, email="freelancer@example.test"):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Sam", "password": PASSWORD},
    )
    assert response.status_code == 201, response.text
    return response


def _merchant(client):
    response = client.put(
        "/api/v1/merchants/paypal",
        json={"client_id": "client-abc", "client_secret": "secret-xyz"},
    )
    assert response.status_code in (200, 201), response.text


def _approved_version(client, db) -> dict:
    """Brief → agreement (stubbed AI) → send → both approvals."""
    from app.routers import agreements as agreement_router

    draft = AgreementDraft(
        title="Marketing site build",
        sections=[
            {
                "section_key": "SCOPE",
                "title": "Scope",
                "body": "A five page marketing site with a blog and a contact form.",
            }
        ],
        milestones=[
            {
                "ordinal": 1,
                "title": "Design",
                "deliverables": ["wireframes"],
                "acceptance": ["signed off"],
                "amount_cents": 12_000,
            },
            {
                "ordinal": 2,
                "title": "Build",
                "deliverables": ["the site"],
                "acceptance": ["it loads"],
                "amount_cents": 18_000,
            },
        ],
    )

    class Stub:
        def generate(self, **kwargs):
            kwargs["validator"](draft)
            return draft

    original = agreement_router.get_ai_client
    agreement_router.get_ai_client = lambda **_: Stub()
    try:
        project = client.post("/api/v1/projects", json=PROJECT).json()
        created = client.post(
            f"/api/v1/projects/{project['id']}/agreement",
            json={"accepted_requirements": ["A five page site"]},
        )
        assert created.status_code == 201, created.text
        version = created.json()
        client.post(f"/api/v1/agreements/{version['id']}/send", json={})
        client.post(f"/api/v1/agreements/{version['id']}/approve", json={"role": "freelancer"})
        client.post(f"/api/v1/agreements/{version['id']}/approve", json={"role": "client"})
        return version
    finally:
        agreement_router.get_ai_client = original


def _milestone_id(version: dict) -> str:
    return version["milestones"][0]["id"]


def _create_payment(client, milestone_id, key="idem-key-0001"):
    return client.post(
        f"/api/v1/milestones/{milestone_id}/payment", json={}, headers={"Idempotency-Key": key}
    )


# -------------------------------------------------------------------- creation


def test_a_payment_reads_the_amount_from_the_database(client, db, paypal):
    stub = paypal(StubPayPal())
    _signup(client)
    _merchant(client)
    version = _approved_version(client, db)

    response = _create_payment(client, _milestone_id(version))

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["paypal_order_id"] == "5O190127TN364715T"
    assert body["approve_url"] == "https://paypal.test/approve"
    assert body["paypal_client_id"] == "client-abc"
    # The amount the client asked PayPal for is the row's, to the cent.
    assert stub.orders[0]["amount"] == Decimal("120.00")
    assert stub.orders[0]["currency"] == "USD"

    stored = db.get(Payment, uuid.UUID(body["payment_id"]))
    assert stored.amount == Decimal("120.00")
    assert stored.status == "CREATED"


def test_the_same_idempotency_key_returns_the_same_payment(client, db, paypal):
    """A double-click must not buy the work twice. The key is UNIQUE in the
    database so a race loses at the constraint, not in application logic."""
    stub = paypal(StubPayPal())
    _signup(client)
    _merchant(client)
    version = _approved_version(client, db)

    first = _create_payment(client, _milestone_id(version))
    second = _create_payment(client, _milestone_id(version))

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["payment_id"] == first.json()["payment_id"]
    assert len(stub.orders) == 1


def test_a_second_live_payment_for_the_same_milestone_is_refused(client, db, paypal):
    paypal(StubPayPal())
    _signup(client)
    _merchant(client)
    version = _approved_version(client, db)
    _create_payment(client, _milestone_id(version))

    response = _create_payment(client, _milestone_id(version), key="a-different-key")

    assert response.status_code == 409
    assert response.json()["code"] == "PAYMENT_ALREADY_ACTIVE"


def test_no_connected_merchant_is_a_conflict_not_a_crash(client, db, paypal):
    paypal(StubPayPal())
    _signup(client)
    version = _approved_version(client, db)

    response = _create_payment(client, _milestone_id(version))

    assert response.status_code == 409
    assert response.json()["code"] == "MERCHANT_NOT_CONNECTED"


def test_paying_a_milestone_of_an_unapproved_version_is_refused(client, db, paypal):
    """The product's claim is that money follows the approved scope. Without
    this, a draft could be paid and the claim is decoration."""
    from app.routers import agreements as agreement_router

    paypal(StubPayPal())
    _signup(client)
    _merchant(client)

    draft = AgreementDraft(
        title="Marketing site build",
        sections=[
            {
                "section_key": "SCOPE",
                "title": "Scope",
                "body": "A five page marketing site with a blog and a contact form.",
            }
        ],
        milestones=[
            {
                "ordinal": 1,
                "title": "Design",
                "deliverables": ["wireframes"],
                "acceptance": ["signed off"],
                "amount_cents": 30_000,
            }
        ],
    )

    class Stub:
        def generate(self, **kwargs):
            kwargs["validator"](draft)
            return draft

    original = agreement_router.get_ai_client
    agreement_router.get_ai_client = lambda **_: Stub()
    try:
        project = client.post("/api/v1/projects", json=PROJECT).json()
        version = client.post(
            f"/api/v1/projects/{project['id']}/agreement",
            json={"accepted_requirements": ["A five page site"]},
        ).json()
    finally:
        agreement_router.get_ai_client = original

    response = _create_payment(client, _milestone_id(version))

    assert response.status_code == 409
    assert response.json()["code"] == "AGREEMENT_NOT_APPROVED"


# -------------------------------------------------------------------- capture


def test_a_verified_capture_marks_the_payment_and_the_milestone(client, db, paypal):
    stub = paypal(StubPayPal())
    _signup(client)
    _merchant(client)
    version = _approved_version(client, db)
    payment = _create_payment(client, _milestone_id(version)).json()

    response = client.post(
        f"/api/v1/payments/{payment['payment_id']}/capture",
        json={"paypal_order_id": "5O190127TN364715T"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "CAPTURED"
    assert body["captured_at"] is not None
    assert body["paypal_capture_id"] == "3C679366HH908993F"
    assert stub.captures[0]["expected_amount"] == Decimal("120.00")

    milestone = db.get(Milestone, uuid.UUID(_milestone_id(version)))
    assert milestone.status == "PAID"

    # A-2: the absence of a payee is recorded, not invented.
    event = db.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "PAYMENT_CAPTURED")
    ).one()
    assert event.payload["payee_merchant_id"] is None
    assert event.payload["payee_present"] is False


def test_a_capture_that_cannot_prove_itself_is_reconciliation_required(client, db, paypal):
    paypal(StubPayPal(failure=PayPalVerificationError("amount did not match")))
    _signup(client)
    _merchant(client)
    version = _approved_version(client, db)
    payment = _create_payment(client, _milestone_id(version)).json()

    response = client.post(
        f"/api/v1/payments/{payment['payment_id']}/capture",
        json={"paypal_order_id": "5O190127TN364715T"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "RECONCILIATION_REQUIRED"

    stored = db.get(Payment, uuid.UUID(payment["payment_id"]))
    assert stored.status == "CREATED", "an unproven capture must not be recorded as paid"
    assert stored.captured_at is None


def test_capturing_with_the_wrong_order_id_is_refused(client, db, paypal):
    stub = paypal(StubPayPal())
    _signup(client)
    _merchant(client)
    version = _approved_version(client, db)
    payment = _create_payment(client, _milestone_id(version)).json()

    response = client.post(
        f"/api/v1/payments/{payment['payment_id']}/capture",
        json={"paypal_order_id": "SOMEONEELSESORDER"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "ORDER_MISMATCH"
    assert stub.captures == [], "no capture may be attempted for another order"


def test_capturing_twice_is_refused_on_state(client, db, paypal):
    paypal(StubPayPal())
    _signup(client)
    _merchant(client)
    version = _approved_version(client, db)
    payment = _create_payment(client, _milestone_id(version)).json()
    client.post(
        f"/api/v1/payments/{payment['payment_id']}/capture",
        json={"paypal_order_id": "5O190127TN364715T"},
    )

    again = client.post(
        f"/api/v1/payments/{payment['payment_id']}/capture",
        json={"paypal_order_id": "5O190127TN364715T"},
    )

    assert again.status_code == 409
    assert again.json()["code"] == "PAYMENT_STATE_INVALID"


def test_another_users_payment_is_404(client, db, paypal):
    paypal(StubPayPal())
    _signup(client, email="owner@example.test")
    _merchant(client)
    version = _approved_version(client, db)
    payment = _create_payment(client, _milestone_id(version)).json()

    _signup(client, email="intruder@example.test")

    assert client.get(f"/api/v1/payments/{payment['payment_id']}").status_code == 404
    assert (
        client.post(
            f"/api/v1/payments/{payment['payment_id']}/capture",
            json={"paypal_order_id": "5O190127TN364715T"},
        ).status_code
        == 404
    )
