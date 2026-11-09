"""The scope-check endpoint: where a guess becomes a record.

docs/08 §6. The interesting assertions are not the happy path — they are that a
fabricated citation is *persisted as a fabrication* (`NEEDS_CLARIFICATION` with a
`downgrade_reason`) and that `section_id` in the response was produced by the
server, never by the model.
"""

from __future__ import annotations

import uuid

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
    MerchantAccount,
    Milestone,
    Payment,
    Project,
    User,
    WebhookEvent,
)

PASSWORD = "correct horse battery staple"

BODY = (
    "The supplier will design and build a five page marketing site, including a "
    "blog index and a contact form. Copywriting, photography and paid "
    "advertising are not included."
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
            MerchantAccount,
            Project,
            User,
        ):
            session.execute(delete(model))
        session.commit()
    finally:
        session.close()
    yield


class StubAI:
    def __init__(self, analysis: ChangeAnalysis) -> None:
        self.analysis = analysis

    def generate(self, **kwargs):
        return self.analysis


@pytest.fixture
def ai(monkeypatch):
    def install(analysis: ChangeAnalysis) -> None:
        from app.routers import changes as router_module

        monkeypatch.setattr(router_module, "get_ai_client", lambda **_: StubAI(analysis))

    return install


def _signup(client, email="freelancer@example.test"):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Sam", "password": PASSWORD},
    )
    assert response.status_code == 201, response.text


def _version(client) -> dict:
    """A project with an APPROVED version whose SCOPE section is known text."""
    from app.routers import agreements as agreement_router

    draft = AgreementDraft(
        title="Marketing site build",
        sections=[
            {"section_key": "SCOPE", "title": "Scope", "body": BODY},
            {
                "section_key": "EXCLUSIONS",
                "title": "Exclusions",
                "body": "Copywriting, photography and paid advertising are not included.",
            },
        ],
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

    class Stub:
        def generate(self, **kwargs):
            kwargs["validator"](draft)
            return draft

    original = agreement_router.get_ai_client
    agreement_router.get_ai_client = lambda **_: Stub()
    try:
        project = client.post(
            "/api/v1/projects",
            json={
                "name": "Marketing site",
                "client_email": "client@example.test",
                "brief": "A five page marketing site with a blog and a contact form.",
                "budget_amount": "300.00",
            },
        ).json()
        created = client.post(
            f"/api/v1/projects/{project['id']}/agreement",
            json={"accepted_requirements": ["A five page site"]},
        )
        assert created.status_code == 201, created.text
        version = created.json()
        client.post(f"/api/v1/agreements/{version['id']}/send", json={})
        client.post(f"/api/v1/agreements/{version['id']}/approve", json={"role": "freelancer"})
        client.post(f"/api/v1/agreements/{version['id']}/approve", json={"role": "client"})
        return {**version, "project_id": project["id"]}
    finally:
        agreement_router.get_ai_client = original


def _check(client, project_id, text="Can you also add Google login?"):
    return client.post(f"/api/v1/projects/{project_id}/changes", json={"raw_text": text})


def _item(**overrides) -> ScopeItem:
    base = {
        "feature": "Google login",
        "classification": "OUT_OF_SCOPE",
        "explanation": "Sign-in is limited to email and password.",
        "confidence": "high",
        "evidence": [Citation(section_key="SCOPE", quote="a five page marketing site")],
    }
    return ScopeItem(**{**base, **overrides})


# ------------------------------------------------------------------ the happy path


def test_a_resolved_citation_is_persisted_with_the_section_id(client, db, ai):
    ai(ChangeAnalysis(items=[_item()]))
    _signup(client)
    version = _version(client)

    response = _check(client, version["project_id"])

    assert response.status_code == 201, response.text
    body = response.json()
    item = body["items"][0]
    assert item["classification"] == "OUT_OF_SCOPE"
    assert item["downgrade_reason"] is None
    # Produced by the server, not by the model: the model only knows section_key.
    section_id = item["evidence"][0]["section_id"]
    assert section_id == version["sections"][0]["id"]

    stored = db.scalars(select(ChangeItem)).one()
    assert stored.evidence[0]["section_id"] == section_id
    assert db.scalars(select(ChangeRequest)).one().base_version_id == uuid.UUID(version["id"])


def test_the_request_records_the_version_it_was_compared_against(client, db, ai):
    """`base_version_id` is the guarantee that the comparison never moves."""
    ai(ChangeAnalysis(items=[_item()]))
    _signup(client)
    version = _version(client)

    _check(client, version["project_id"])

    stored = db.scalars(select(ChangeRequest)).one()
    assert str(stored.base_version_id) == version["id"]


# ------------------------------------------------------------------ the refusals


def test_a_fabricated_citation_is_persisted_as_a_downgrade_with_its_reason(client, db, ai):
    """The whole point. It must be visible in the record, not just in memory."""
    ai(
        ChangeAnalysis(
            items=[
                _item(
                    evidence=[Citation(section_key="SCOPE", quote="Google login is included")],
                )
            ]
        )
    )
    _signup(client)
    version = _version(client)

    response = _check(client, version["project_id"])

    assert response.status_code == 201
    item = response.json()["items"][0]
    assert item["classification"] == "NEEDS_CLARIFICATION"
    assert item["confidence"] == "low"
    assert item["evidence"] == []
    assert "SCOPE" in item["downgrade_reason"]

    stored = db.scalars(select(ChangeItem)).one()
    assert stored.classification == "NEEDS_CLARIFICATION"
    assert stored.evidence == []
    assert stored.downgrade_reason


def test_already_included_never_carries_a_price(client, db, ai):
    """Charging for work already sold is the failure this product exists to
    prevent, so the API makes the field unavailable rather than zero."""
    ai(
        ChangeAnalysis(
            items=[
                _item(
                    classification="ALREADY_INCLUDED",
                    evidence=[Citation(section_key="SCOPE", quote="a contact form")],
                )
            ]
        )
    )
    _signup(client)
    version = _version(client)

    response = _check(client, version["project_id"])

    assert response.status_code == 201
    assert response.json()["items"][0]["suggested_cost"] is None


def test_a_change_request_without_an_approved_version_is_refused(client, db, ai):
    ai(ChangeAnalysis(items=[_item()]))
    _signup(client)
    project = client.post(
        "/api/v1/projects",
        json={
            "name": "No agreement",
            "client_email": "c@example.test",
            "brief": "A brief long enough to pass validation easily.",
            "budget_amount": "300.00",
        },
    ).json()

    response = _check(client, project["id"])

    assert response.status_code == 409
    assert response.json()["code"] == "NO_APPROVED_VERSION"


def test_another_users_change_request_is_404(client, db, ai):
    ai(ChangeAnalysis(items=[_item()]))
    _signup(client, email="owner@example.test")
    version = _version(client)
    request_id = _check(client, version["project_id"]).json()["change_request"]["id"]

    _signup(client, email="intruder@example.test")

    assert client.get(f"/api/v1/changes/{request_id}").status_code == 404
