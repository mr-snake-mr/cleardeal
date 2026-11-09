"""docs/12 §4, the scenarios that are not the golden path.

E5 is the one written here: a request that mixes work already included, work
outside the scope, and a phrase nobody can price. Three items, three different
verdicts, and the point is that they do not blur into one another — the whole
product is the distinction between "you already paid for this" and "this is
extra", and a model that returns one blended answer is useless.

The others live where they belong: E1 in `test_e2e_golden_path.py`, E3 and E4 in
`test_change_requests.py`, E7 in `test_webhooks.py`, E8 in `test_agreements.py`,
E9 in `test_payments.py` (the request has no amount field to tamper with).
E2 needs a live sandbox; E6 needs the change-approval flow, which is not built.
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete

from app.ai.schemas import (
    AgreementDraft,
    BriefAnalysis,
    ChangeAnalysis,
    Citation,
    Requirement,
    ScopeItem,
)
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
SCOPE = (
    "The supplier will design and build a five page marketing site, including a "
    "blog index and a contact form. Copywriting, photography and paid advertising "
    "are not included."
)


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


def _mixed() -> ChangeAnalysis:
    return ChangeAnalysis(
        items=[
            ScopeItem(
                feature="A contact form",
                classification="ALREADY_INCLUDED",
                explanation="The scope names a contact form.",
                confidence="high",
                evidence=[
                    Citation(
                        section_key="SCOPE",
                        quote="including a blog index and a contact form",
                    )
                ],
            ),
            ScopeItem(
                feature="Google login",
                classification="OUT_OF_SCOPE",
                explanation="Sign-in is not mentioned anywhere.",
                confidence="medium",
                evidence=[Citation(section_key="SCOPE", quote="a five page marketing site")],
            ),
            ScopeItem(
                feature="Make it pop",
                classification="NEEDS_CLARIFICATION",
                explanation="No measurable meaning.",
                confidence="low",
                evidence=[],
            ),
        ]
    )


@pytest.fixture
def provider(monkeypatch):
    from app.routers import agreements, changes, projects

    monkeypatch.setattr(
        projects,
        "get_ai_client",
        lambda **_: type(
            "S",
            (),
            {
                "generate": lambda self, **kw: BriefAnalysis(
                    requirements=[
                        Requirement(
                            text="A five page marketing site",
                            source="explicit",
                            quote="five page marketing site",
                        )
                    ],
                    ambiguities=[],
                    risks=[],
                    assumptions=[],
                )
            },
        )(),
    )

    draft = AgreementDraft(
        title="Marketing site build",
        sections=[
            {"section_key": "SCOPE", "title": "Scope", "body": SCOPE},
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

    class AgreementStub:
        def generate(self, **kwargs):
            kwargs["validator"](draft)
            return draft

    monkeypatch.setattr(agreements, "get_ai_client", lambda **_: AgreementStub())

    analysis = _mixed()

    class ChangeStub:
        def generate(self, **kwargs):
            return analysis

    monkeypatch.setattr(changes, "get_ai_client", lambda **_: ChangeStub())


def test_e5_a_mixed_request_returns_three_distinct_verdicts(client, provider, client_side):
    client.post(
        "/api/v1/auth/register",
        json={"email": "owner@example.test", "name": "Sam", "password": PASSWORD},
    )
    project = client.post(
        "/api/v1/projects",
        json={
            "name": "Marketing site",
            "client_email": "client@example.test",
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
    client_side(client, project["id"], version["id"])

    response = client.post(
        f"/api/v1/projects/{project['id']}/changes",
        json={"raw_text": "Add Google login, and make the whole thing pop."},
    )

    assert response.status_code == 201, response.text
    items = {item["feature"]: item for item in response.json()["items"]}
    assert set(items) == {"A contact form", "Google login", "Make it pop"}

    included = items["A contact form"]
    assert included["classification"] == "ALREADY_INCLUDED"
    assert included["suggested_cost"] is None, "work already sold must never carry a price"
    assert included["evidence"][0]["quote"].startswith("including a blog index")

    extra = items["Google login"]
    assert extra["classification"] == "OUT_OF_SCOPE"
    assert extra["evidence"], "an out-of-scope verdict needs its citation"
    assert extra["downgrade_reason"] is None

    unclear = items["Make it pop"]
    assert unclear["classification"] == "NEEDS_CLARIFICATION"
    assert unclear["evidence"] == []
    assert unclear["confidence"] == "low"
