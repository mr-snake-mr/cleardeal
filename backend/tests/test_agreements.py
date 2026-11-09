"""Agreements: the version is the product. It must not move underneath anyone.

Contract: docs/08 §4. Every test below states the behaviour that would have to
be deleted for it to pass — the ones that matter are the two refusals (a `send`
that no longer sums, an `approve` of a superseded version), because those are
the only things standing between a demo and a document that says one number and
pays another.
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
    Milestone,
    Project,
    User,
)

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
            Milestone,
            AgreementSection,
            AgreementVersion,
            Agreement,
            Project,
            User,
        ):
            session.execute(delete(model))
        session.commit()
    finally:
        session.close()
    yield


def _draft(split=(12_000, 18_000)) -> AgreementDraft:
    return AgreementDraft(
        title="Marketing site build",
        sections=[
            {
                "section_key": "SCOPE",
                "title": "Scope",
                "body": "A five page marketing site, including a blog and a contact form.",
            }
        ],
        milestones=[
            {
                "ordinal": index + 1,
                "title": f"Stage {index + 1}",
                "deliverables": ["a page"],
                "acceptance": ["it loads"],
                "amount_cents": amount,
            }
            for index, amount in enumerate(split)
        ],
    )


class StubClient:
    """Stands in for Gemini, and honours the validator it is handed."""

    def __init__(self, draft: AgreementDraft) -> None:
        self.draft = draft

    def generate(self, **kwargs):
        # Mirrors the real client's contract: a validator's `ValueError` becomes
        # `AiInvalidOutput`. A stub that let it escape would test a call path the
        # provider path does not have.
        from app.ai.client import AiInvalidOutput

        validator = kwargs.get("validator")
        if validator is not None:
            try:
                validator(self.draft)
            except ValueError as exc:
                raise AiInvalidOutput(str(exc)) from exc
        return self.draft

    # `draft_agreement` also reads this through the router's error mapping.
    model = "stub"


@pytest.fixture
def ai(monkeypatch):
    """One place to swap the provider out; the tests still go through HTTP."""

    def install(draft: AgreementDraft) -> None:
        from app.routers import agreements as router_module

        monkeypatch.setattr(router_module, "get_ai_client", lambda **_: StubClient(draft))

    install(_draft())
    return install


def _signup(client, email="freelancer@example.test"):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Sam", "password": PASSWORD},
    )
    assert response.status_code == 201, response.text
    return response


def _project(client) -> dict:
    response = client.post("/api/v1/projects", json=PROJECT)
    assert response.status_code == 201, response.text
    return response.json()


def _agreement(client, project_id, accepted=("A five page site",)):
    return client.post(
        f"/api/v1/projects/{project_id}/agreement",
        json={"accepted_requirements": list(accepted)},
    )


# ------------------------------------------------------------------ creation


def test_creating_a_draft_persists_the_version_sections_and_milestones(client, ai, db):
    _signup(client)
    project = _project(client)

    response = _agreement(client, project["id"])

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["version_no"] == 1
    assert body["status"] == "DRAFT"
    assert body["budget_amount"] == "300.00"
    assert [m["amount"] for m in body["milestones"]] == ["120.00", "180.00"]
    assert body["sections"][0]["section_key"] == "SCOPE"
    assert len(body["content_hash"]) == 64


def test_the_hash_is_over_content_not_over_the_row(client, ai, db):
    """If the hash were random or timestamped, 'the document cannot change'
    would be unfalsifiable. It must move when a section body moves."""
    _signup(client)
    project = _project(client)
    version_id = _agreement(client, project["id"]).json()["id"]

    from app.services.agreements import content_hash_for

    version = db.get(AgreementVersion, uuid.UUID(version_id))
    before = content_hash_for(version)

    section = db.scalars(
        select(AgreementSection).where(AgreementSection.version_id == version.id)
    ).one()
    section.body = section.body + " Amended."
    db.commit()
    db.refresh(version)

    assert content_hash_for(version) != before


def test_a_second_draft_supersedes_the_first_and_becomes_current(client, ai, db):
    _signup(client)
    project = _project(client)
    first = _agreement(client, project["id"]).json()["id"]
    second = _agreement(client, project["id"])

    assert second.status_code == 201, second.text
    assert second.json()["version_no"] == 2

    db.expire_all()
    assert db.get(AgreementVersion, uuid.UUID(first)).status == "SUPERSEDED"
    agreement = db.scalars(select(Agreement)).one()
    assert agreement.current_version == 2


def test_a_draft_that_does_not_sum_to_the_budget_is_refused_at_creation(client, ai):
    """The AI invariant still guards the door; persistence is not a bypass."""
    ai(_draft(split=(10_000, 10_000)))
    _signup(client)
    project = _project(client)

    response = _agreement(client, project["id"])

    assert response.status_code == 502
    assert response.json()["code"] == "AI_INVALID_OUTPUT"


# ----------------------------------------------------------------------- send


def test_send_moves_a_matching_version_to_pending_client(client, ai):
    _signup(client)
    project = _project(client)
    version_id = _agreement(client, project["id"]).json()["id"]

    response = client.post(f"/api/v1/agreements/{version_id}/send", json={})

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "PENDING_CLIENT"


def test_send_refuses_when_the_milestones_no_longer_sum(client, ai, db):
    """Creation checked the total. The row can still be edited afterwards, so
    `send` checks again — this is the last gate before a client sees a number."""
    _signup(client)
    project = _project(client)
    version_id = _agreement(client, project["id"]).json()["id"]

    milestone = db.scalars(
        select(Milestone)
        .join(AgreementVersion, Milestone.version_id == AgreementVersion.id)
        .where(AgreementVersion.id == uuid.UUID(version_id))
        .order_by(Milestone.ordinal)
    ).first()
    milestone.amount = Decimal("100.00")
    db.commit()

    response = client.post(f"/api/v1/agreements/{version_id}/send", json={})

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "MILESTONE_SUM_MISMATCH"
    assert body["details"]["budget"] == "300.00"
    assert body["details"]["sum"] == "280.00"


# ------------------------------------------------------------------- approval


def test_both_approvals_make_the_version_approved_and_leave_the_hash_alone(
    client, ai, client_side
):
    _signup(client)
    project = _project(client)
    created = _agreement(client, project["id"]).json()
    version_id, original_hash = created["id"], created["content_hash"]
    client.post(f"/api/v1/agreements/{version_id}/send", json={})

    first = client.post(
        f"/api/v1/agreements/{version_id}/approve", json={"role": "freelancer"}
    )
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "PENDING_CLIENT"

    second = client_side(client, project["id"], version_id)

    assert second.status_code == 200, second.text
    assert second.json()["status"] == "APPROVED"
    assert second.json()["content_hash"] == original_hash


def test_approving_a_superseded_version_is_refused(client, ai):
    """v1 is not 'the agreement' once v2 exists. Approving it silently would
    approve a document nobody is looking at."""
    _signup(client)
    project = _project(client)
    first = _agreement(client, project["id"]).json()["id"]
    client.post(f"/api/v1/agreements/{first}/send", json={})
    _agreement(client, project["id"])

    response = client.post(f"/api/v1/agreements/{first}/approve", json={"role": "client"})

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "STALE_VERSION"
    assert body["details"] == {"approved_version": 1, "current_version": 2}


def test_reject_cancels_the_version(client, ai):
    _signup(client)
    project = _project(client)
    version_id = _agreement(client, project["id"]).json()["id"]
    client.post(f"/api/v1/agreements/{version_id}/send", json={})

    response = client.post(
        f"/api/v1/agreements/{version_id}/reject", json={"reason": "Scope is wrong."}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"


# ------------------------------------------------------------------ isolation


def test_approve_refuses_a_version_that_no_longer_matches_its_hash(client, ai, db):
    """`content_hash` is only evidence if something re-checks it. Without this,
    a milestone could be re-priced after send and the hash would still match
    nothing that anybody compares."""
    _signup(client)
    project = _project(client)
    version_id = _agreement(client, project["id"]).json()["id"]
    client.post(f"/api/v1/agreements/{version_id}/send", json={})

    section = db.scalars(
        select(AgreementSection).where(AgreementSection.version_id == uuid.UUID(version_id))
    ).one()
    section.body = "Something else entirely, added after the hash was taken."
    db.commit()

    response = client.post(
        f"/api/v1/agreements/{version_id}/approve", json={"role": "freelancer"}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "CONTENT_CHANGED"


def test_get_reports_whether_the_content_still_matches_the_hash(client, ai, db):
    """The read path recomputes, so a reader is never shown a hash that is
    silently stale."""
    _signup(client)
    project = _project(client)
    version_id = _agreement(client, project["id"]).json()["id"]

    assert client.get(f"/api/v1/agreements/{version_id}").json()["content_intact"] is True

    section = db.scalars(
        select(AgreementSection).where(AgreementSection.version_id == uuid.UUID(version_id))
    ).one()
    section.body = "Edited underneath the reader."
    db.commit()

    assert client.get(f"/api/v1/agreements/{version_id}").json()["content_intact"] is False


def test_approve_refuses_when_the_milestones_no_longer_sum(
    client, ai, db, client_side
):
    """`send` is not the only gate: a row can change before approval, and
    APPROVED is the state a payment reads."""
    _signup(client)
    project = _project(client)
    version_id = _agreement(client, project["id"]).json()["id"]
    client.post(f"/api/v1/agreements/{version_id}/send", json={})

    milestone = db.scalars(
        select(Milestone).where(Milestone.version_id == uuid.UUID(version_id))
    ).first()
    milestone.amount = Decimal("150.00")
    db.commit()

    response = client_side(client, project["id"], version_id)

    # The hash moves first, and both refusals are correct; the sum check is the
    # one that names the money.
    assert response.status_code == 409
    assert response.json()["code"] in {"CONTENT_CHANGED", "MILESTONE_SUM_MISMATCH"}


def test_a_second_draft_racing_for_the_same_version_number_is_a_409(client, ai, db):
    """The unique constraint is the real guard; this checks it surfaces as a
    retryable 409 rather than a 500 with a traceback in the log."""
    _signup(client)
    project = _project(client)
    agreement = Agreement(project_id=uuid.UUID(project["id"]), current_version=0)
    db.add(agreement)
    db.flush()
    db.add(
        AgreementVersion(
            agreement_id=agreement.id,
            version_no=1,
            status="SUPERSEDED",
            budget_amount=Decimal("300.00"),
            created_by=db.scalars(select(User)).one().id,
            content_hash="0" * 64,
        )
    )
    db.commit()

    response = _agreement(client, project["id"])

    assert response.status_code == 409
    assert response.json()["code"] == "VERSION_RACE"


def test_another_users_agreement_is_404_not_403(client, ai):
    """403 confirms the id exists; the ids are UUID v4 so that they cannot be
    enumerated (docs/10 §2)."""
    _signup(client, email="owner@example.test")
    project = _project(client)
    version_id = _agreement(client, project["id"]).json()["id"]

    _signup(client, email="intruder@example.test")

    assert client.get(f"/api/v1/agreements/{version_id}").status_code == 404
    assert client.post(f"/api/v1/agreements/{version_id}/send", json={}).status_code == 404
    assert (
        client.post(
            f"/api/v1/agreements/{version_id}/approve", json={"role": "client"}
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/agreements/{version_id}/reject", json={"reason": "not mine"}
        ).status_code
        == 404
    )


