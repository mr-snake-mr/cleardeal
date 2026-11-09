"""The demo path, end to end, through HTTP.

docs/14 scenario 1: a brief becomes an approved agreement, and a change request
is checked against it — with a real citation accepted and a fabricated one
refused, in the same response.

Only the provider is stubbed. Everything else is the shipping code: routing,
validation, persistence, the evidence rule.
"""

from __future__ import annotations

import pytest

from app.ai.schemas import (
    AgreementDraft,
    BriefAnalysis,
    ChangeAnalysis,
    Citation,
    Requirement,
    ScopeItem,
)

PASSWORD = "correct horse battery staple"
BRIEF = (
    "Five page marketing site with a blog index and a contact form. "
    "Budget is 300 dollars. Copywriting and photography are not included."
)
SCOPE_BODY = (
    "The supplier will design and build a five page marketing site, including a "
    "blog index and a contact form. Copywriting and photography are not included."
)


class Stub:
    def __init__(self, payload) -> None:
        self.payload = payload

    def generate(self, **kwargs):
        validator = kwargs.get("validator")
        if validator is not None:
            validator(self.payload)
        return self.payload


@pytest.fixture
def provider(monkeypatch):
    """One seam per module: the routers each build their own client."""

    from app.routers import agreements, changes, projects

    def install():
        monkeypatch.setattr(
            projects,
            "get_ai_client",
            lambda **_: Stub(
                BriefAnalysis(
                    requirements=[
                        Requirement(
                            text="A five page marketing site",
                            source="explicit",
                            quote="Five page marketing site",
                        ),
                        Requirement(text="A blog index", source="inferred", quote=None),
                    ],
                    ambiguities=[],
                    risks=[],
                    assumptions=[],
                )
            ),
        )
        monkeypatch.setattr(
            agreements,
            "get_ai_client",
            lambda **_: Stub(
                AgreementDraft(
                    title="Marketing site build",
                    sections=[
                        {"section_key": "SCOPE", "title": "Scope", "body": SCOPE_BODY},
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
            ),
        )
        monkeypatch.setattr(
            changes,
            "get_ai_client",
            lambda **_: Stub(
                ChangeAnalysis(
                    items=[
                        ScopeItem(
                            feature="Google login",
                            classification="OUT_OF_SCOPE",
                            explanation="Sign-in is email and password only.",
                            confidence="high",
                            evidence=[
                                Citation(section_key="SCOPE", quote="a five page marketing site"),
                            ],
                        ),
                        ScopeItem(
                            feature="Free copywriting",
                            classification="ALREADY_INCLUDED",
                            explanation="Copywriting is included.",
                            confidence="high",
                            evidence=[
                                Citation(section_key="SCOPE", quote="copywriting is included"),
                            ],
                        ),
                    ]
                )
            ),
        )

    return install


def test_the_whole_path_from_brief_to_scope_verdict(client, provider, client_side):
    provider()

    assert (
        client.post(
            "/api/v1/auth/register",
            json={"email": "demo@example.test", "name": "Demo", "password": PASSWORD},
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"email": "demo@example.test", "password": PASSWORD},
        ).status_code
        == 200
    )

    project = client.post(
        "/api/v1/projects",
        json={
            "name": "Marketing site",
            "client_email": "client@example.test",
            "brief": BRIEF,
            "budget_amount": "300.00",
        },
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]

    analysis = client.post(f"/api/v1/projects/{project_id}/analyse")
    assert analysis.status_code == 200, analysis.text
    explicit = [r for r in analysis.json()["requirements"] if r["source"] == "explicit"]
    assert explicit and explicit[0]["quote"] == "Five page marketing site"

    version = client.post(
        f"/api/v1/projects/{project_id}/agreement",
        json={"accepted_requirements": ["A five page marketing site", "A blog index"]},
    )
    assert version.status_code == 201, version.text
    body = version.json()
    assert body["status"] == "DRAFT"
    assert body["content_intact"] is True
    assert sum(float(m["amount"]) for m in body["milestones"]) == 300.00

    assert client.post(f"/api/v1/agreements/{body['id']}/send", json={}).status_code == 200
    client.post(f"/api/v1/agreements/{body['id']}/approve", json={"role": "freelancer"})
    approved = client_side(client, project_id, body["id"])
    assert approved.json()["status"] == "APPROVED"

    checked = client.post(
        f"/api/v1/projects/{project_id}/changes",
        json={"raw_text": "Can you also add Google login and throw in the copywriting?"},
    )
    assert checked.status_code == 201, checked.text
    items = checked.json()["items"]

    by_feature = {item["feature"]: item for item in items}

    good = by_feature["Google login"]
    assert good["classification"] == "OUT_OF_SCOPE"
    # The server resolved the key to a real id.
    assert good["evidence"][0]["section_id"] == body["sections"][0]["id"]

    fabricated = by_feature["Free copywriting"]
    assert fabricated["classification"] == "NEEDS_CLARIFICATION"
    assert fabricated["evidence"] == []
    # "copywriting is included" is not in the section — the section says it is
    # NOT included. The downgrade names the section and keeps the guess visible.
    assert fabricated["downgrade_reason"]
    assert fabricated["confidence"] == "low"
