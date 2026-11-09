"""E6: an accepted change becomes the next version, drafted but not priced.

The rule that keeps this honest: **the AI never writes a price and the change
flow never invents one.** Approving a change copies the signed version, appends a
`CHANGES` section recording what was accepted with its citation, and leaves the
money exactly as it was. The human then re-prices the draft — and the sum
invariant refuses to let it leave `DRAFT` until the milestones match the budget.

So E6 is two pieces: approving into `v(n+1)`, and an edit path for a draft. Both
are tested here because either alone is a dead end.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.ai.schemas import AgreementDraft, ChangeAnalysis, Citation, ScopeItem
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
CLIENT_EMAIL = "client@example.test"
SCOPE = (
    "The supplier will design and build a five page marketing site, including a "
    "blog index and a contact form. Copywriting and photography are not included."
)


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


@pytest.fixture
def provider(monkeypatch):
    from app.routers import agreements, changes

    draft = AgreementDraft(
        title="Marketing site build",
        sections=[{"section_key": "SCOPE", "title": "Scope", "body": SCOPE}],
        milestones=[
            {
                "ordinal": 1,
                "title": "Build",
                "deliverables": ["the site"],
                "acceptance": ["it loads"],
                "amount_cents": 30_000,
            }
        ],
    )

    class AgreementStub:
        def generate(self, **kwargs):
            kwargs["validator"](draft)
            return draft

    analysis = ChangeAnalysis(
        items=[
            ScopeItem(
                feature="Google login",
                classification="OUT_OF_SCOPE",
                explanation="Sign-in is not in the scope.",
                confidence="high",
                evidence=[Citation(section_key="SCOPE", quote="a five page marketing site")],
            )
        ]
    )

    class ChangeStub:
        def generate(self, **kwargs):
            return analysis

    monkeypatch.setattr(agreements, "get_ai_client", lambda **_: AgreementStub())
    monkeypatch.setattr(changes, "get_ai_client", lambda **_: ChangeStub())


def _signup(client, email="owner@example.test"):
    return client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Sam", "password": PASSWORD},
    )


def _agreed_project(client, client_side) -> tuple[dict, dict]:
    project = client.post(
        "/api/v1/projects",
        json={
            "name": "Marketing site",
            "client_email": CLIENT_EMAIL,
            "brief": "Five page marketing site with a blog index and a contact form.",
            "budget_amount": "300.00",
        },
    ).json()
    version = client.post(
        f"/api/v1/projects/{project['id']}/agreement",
        json={"accepted_requirements": ["A five page marketing site"]},
    ).json()
    client.post(f"/api/v1/agreements/{version['id']}/send", json={})
    client.post(f"/api/v1/agreements/{version['id']}/approve", json={"role": "freelancer"})
    assert client_side(client, project["id"], version["id"]).status_code == 200
    return project, version


def _change(client, project_id) -> dict:
    response = client.post(
        f"/api/v1/projects/{project_id}/changes",
        json={"raw_text": "Can you also add Google login?"},
    )
    assert response.status_code == 201, response.text
    return response.json()["change_request"]


# --------------------------------------------------------- approving into v2


def test_both_approvals_create_the_next_version_and_change_nothing_about_money(
    client, db, provider, client_side
):
    _signup(client)
    project, version = _agreed_project(client, client_side)
    request = _change(client, project["id"])

    first = client.post(f"/api/v1/changes/{request['id']}/approve", json={"role": "freelancer"})
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "PROPOSED"

    second = client_side(
        client, project["id"], request["id"], path=f"/changes/{request['id']}/approve"
    )

    assert second.status_code == 200, second.text
    assert second.json()["status"] == "APPROVED"
    next_version = second.json()["resulting_version_id"]
    assert next_version

    db.expire_all()
    draft = db.get(AgreementVersion, uuid.UUID(next_version))
    assert draft.version_no == 2
    assert draft.status == "DRAFT"
    assert draft.budget_amount == Decimal("300.00"), "the flow does not price anything"
    assert [m.amount for m in draft.milestones] == [Decimal("300.00")]
    keys = [s.section_key for s in draft.sections]
    assert keys == ["SCOPE", "CHANGES"]
    assert "Google login" in draft.sections[-1].body
    assert "a five page marketing site" in draft.sections[-1].body

    # The signed version stays signed; it simply stops being current.
    base = db.get(AgreementVersion, uuid.UUID(version["id"]))
    assert base.status == "APPROVED"
    assert db.scalars(select(Agreement)).one().current_version == 2


def test_the_owner_cannot_approve_a_change_as_the_client(client, db, provider, client_side):
    _signup(client)
    project, _ = _agreed_project(client, client_side)
    request = _change(client, project["id"])

    response = client.post(f"/api/v1/changes/{request['id']}/approve", json={"role": "client"})

    assert response.status_code == 409
    assert response.json()["code"] == "ROLE_NOT_YOURS"


# ------------------------------------------------------------- pricing a draft


def test_a_draft_can_be_repriced_and_then_sent(client, db, provider, client_side):
    _signup(client)
    project, _ = _agreed_project(client, client_side)
    request = _change(client, project["id"])
    client.post(f"/api/v1/changes/{request['id']}/approve", json={"role": "freelancer"})
    next_id = client_side(
        client, project["id"], request["id"], path=f"/changes/{request['id']}/approve"
    ).json()["resulting_version_id"]

    response = client.patch(
        f"/api/v1/agreements/{next_id}",
        json={
            "budget_amount": "400.00",
            "milestones": [
                {
                    "ordinal": 1,
                    "title": "Build",
                    "deliverables": ["the site"],
                    "acceptance": ["it loads"],
                    "amount": "300.00",
                },
                {
                    "ordinal": 2,
                    "title": "Google login",
                    "deliverables": ["sign-in"],
                    "acceptance": ["a user signs in"],
                    "amount": "100.00",
                },
            ],
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["budget_amount"] == "400.00"
    assert len(response.json()["milestones"]) == 2
    assert response.json()["content_intact"] is True

    sent = client.post(f"/api/v1/agreements/{next_id}/send", json={})
    assert sent.status_code == 200, sent.text


def test_a_repriced_draft_that_does_not_sum_is_refused(client, db, provider, client_side):
    _signup(client)
    project, _ = _agreed_project(client, client_side)
    request = _change(client, project["id"])
    client.post(f"/api/v1/changes/{request['id']}/approve", json={"role": "freelancer"})
    next_id = client_side(
        client, project["id"], request["id"], path=f"/changes/{request['id']}/approve"
    ).json()["resulting_version_id"]

    response = client.patch(
        f"/api/v1/agreements/{next_id}",
        json={
            "budget_amount": "400.00",
            "milestones": [
                {
                    "ordinal": 1,
                    "title": "Build",
                    "deliverables": ["the site"],
                    "acceptance": ["it loads"],
                    "amount": "300.00",
                }
            ],
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "MILESTONE_SUM_MISMATCH"


def test_an_approved_version_cannot_be_edited(client, db, provider, client_side):
    _signup(client)
    _, version = _agreed_project(client, client_side)

    response = client.patch(
        f"/api/v1/agreements/{version['id']}",
        json={"budget_amount": "999.00", "milestones": []},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "INVALID_STATE"
