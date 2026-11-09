"""Project update and delete — the rest of block 1.6.

Two decisions are worth a test each:

- PATCH is partial: a field that is absent is untouched, and `budget_amount` is
  *not* patchable at all. Money is set once, when the brief is agreed; editing a
  budget after an agreement exists would silently invalidate the sum invariant
  that every payment rests on.
- DELETE refuses when money exists. The foreign keys cascade from project to
  agreement to milestone, and `payments` is RESTRICT — so the database would
  refuse anyway. A 409 that names the reason is better than a 500 that does not.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.database import SessionLocal
from app.models import (
    Agreement,
    AgreementSection,
    AgreementVersion,
    AuditEvent,
    ChangeItem,
    ChangeRequest,
    Invitation,
    MerchantAccount,
    Milestone,
    Payment,
    Project,
    User,
    WebhookEvent,
)

PASSWORD = "correct horse battery staple"

PROJECT = {
    "name": "Marketing site",
    "client_email": "client@example.test",
    "brief": "A five page marketing site with a blog index and a contact form.",
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
            ChangeItem,
            ChangeRequest,
            WebhookEvent,
            Payment,
            Milestone,
            AgreementSection,
            AgreementVersion,
            Agreement,
            Invitation,
            MerchantAccount,
            Project,
            User,
        ):
            session.execute(delete(model))
        session.commit()
    finally:
        session.close()
    yield


def _signup(client, email="freelancer@example.test"):
    return client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Sam", "password": PASSWORD},
    )


def _project(client) -> dict:
    response = client.post("/api/v1/projects", json=PROJECT)
    assert response.status_code == 201, response.text
    return response.json()


# ------------------------------------------------------------------- updating


def test_patching_one_field_leaves_the_others_alone(client, db):
    _signup(client)
    project = _project(client)

    response = client.patch(f"/api/v1/projects/{project['id']}", json={"name": "Renamed"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "Renamed"
    assert body["budget_amount"] == "300.00"
    assert body["brief"] == PROJECT["brief"]


def test_the_budget_cannot_be_patched(client, db):
    """One figure is checked against milestones on every send and approve.
    Letting it move after the fact would invalidate that check retroactively."""
    _signup(client)
    project = _project(client)

    response = client.patch(
        f"/api/v1/projects/{project['id']}", json={"budget_amount": "999.00"}
    )

    assert response.status_code == 422
    assert "budget" in str(response.json()).lower()


def test_an_empty_patch_is_refused(client, db):
    _signup(client)
    project = _project(client)

    assert client.patch(f"/api/v1/projects/{project['id']}", json={}).status_code == 422


def test_a_non_owner_cannot_patch(client, db):
    _signup(client, email="owner@example.test")
    project = _project(client)

    _signup(client, email="intruder@example.test")

    assert (
        client.patch(f"/api/v1/projects/{project['id']}", json={"name": "Mine now"}).status_code
        == 404
    )


# ------------------------------------------------------------------- deleting


def test_deleting_a_project_without_money_removes_it(client, db):
    _signup(client)
    project = _project(client)

    response = client.delete(f"/api/v1/projects/{project['id']}")

    assert response.status_code == 204
    assert client.get(f"/api/v1/projects/{project['id']}").status_code == 404


def test_deleting_a_project_that_holds_a_payment_is_refused(client, db):
    """The FK is RESTRICT; this turns the integrity error into a sentence."""
    _signup(client)
    project = _project(client)

    user = db.scalars(select(User)).one()
    agreement = Agreement(project_id=uuid.UUID(project["id"]), current_version=1)
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
        title="Build",
        deliverables=["the site"],
        acceptance=["it loads"],
        amount=Decimal("300.00"),
    )
    db.add(milestone)
    db.flush()
    db.add(
        Payment(
            project_id=uuid.UUID(project["id"]),
            milestone_id=milestone.id,
            amount=Decimal("300.00"),
            currency="USD",
            status="CAPTURED",
            merchant_user_id=user.id,
            paypal_order_id="ORDER-1",
            idempotency_key=uuid.uuid4().hex,
        )
    )
    db.commit()

    response = client.delete(f"/api/v1/projects/{project['id']}")

    assert response.status_code == 409
    assert response.json()["code"] == "PROJECT_HAS_PAYMENTS"
    assert client.get(f"/api/v1/projects/{project['id']}").status_code == 200
