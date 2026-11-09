"""The audit's findings, as tests — written before the fix.

The critical one: `accept` used to issue a session for whichever account held the
invited address. Since the invitation response hands the inviter the token, any
registered user could invite a victim's email, read the token from their own
response, and accept it — receiving a session for the victim's account. The
mailbox was never involved.

The rule that closes it: **a magic link may only sign in a principal the link
created.** If the address already has an account, that account signs in with its
own credentials; the link cannot stand in for them.
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from app.ai.schemas import AgreementDraft
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
def ai(monkeypatch):
    from app.routers import agreements

    draft = AgreementDraft(
        title="Marketing site build",
        sections=[
            {
                "section_key": "SCOPE",
                "title": "Scope",
                "body": "A five page marketing site with a blog index and a contact form.",
            }
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

    monkeypatch.setattr(agreements, "get_ai_client", lambda **_: Stub())


def _signup(client, email="freelancer@example.test"):
    return client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Sam", "password": PASSWORD},
    )


def _project(client, email=CLIENT_EMAIL) -> dict:
    response = client.post(
        "/api/v1/projects",
        json={
            "name": "Marketing site",
            "client_email": email,
            "brief": "A five page marketing site with a blog index and a contact form.",
            "budget_amount": "300.00",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


# ------------------------------------------------------- the takeover, closed


def test_a_link_cannot_sign_in_an_account_that_already_exists(client, db, ai):
    """The attack: invite the victim's address, read the token from your own
    response, accept it, and hold their session."""
    # The victim is an ordinary registered user with projects of their own.
    victim = _signup(client, email="victim@example.test")
    assert victim.status_code == 201
    # Then an attacker registers, so this client is signed in as the attacker.
    assert _signup(client, email="attacker@example.test").status_code == 201

    project = _project(client, email="victim@example.test")
    invited = client.post(
        f"/api/v1/projects/{project['id']}/invitations",
        json={"email": "victim@example.test"},
    )
    assert invited.status_code == 201, invited.text
    token = invited.json()["invite_url"].rsplit("/", 1)[-1]

    # Start from a clean jar: the attacker is not signed in at all.
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(), base_url="https://testserver") as anonymous:
        response = anonymous.post(f"/api/v1/invitations/{token}/accept")

        assert response.status_code == 409, "a link must not mint a session for an existing account"
        assert response.json()["code"] == "EMAIL_ALREADY_REGISTERED"
        assert "cleardeal_session" not in anonymous.cookies
        # And nothing was consumed, so the real owner of the address can still
        # sign in normally and approve.
        assert anonymous.get("/api/v1/projects").status_code == 401


def test_the_owner_cannot_be_their_own_client(client, db, ai):
    """Self-invitation was the cheap version of the same hole."""
    _signup(client, email="owner@example.test")
    project = _project(client, email="owner@example.test")

    response = client.post(
        f"/api/v1/projects/{project['id']}/invitations", json={"email": "owner@example.test"}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "SELF_INVITATION"


def test_an_invited_address_is_normalised(client, db, ai):
    """`" Client@Example.Test "` must not create a second principal for an
    address that is already a user, nor a shadow row that can never sign."""
    _signup(client)
    project = _project(client)

    invited = client.post(
        f"/api/v1/projects/{project['id']}/invitations",
        json={"email": " CLIENT@Example.TEST "},
    )

    assert invited.status_code == 201, invited.text
    assert invited.json()["email"] == CLIENT_EMAIL


# ------------------------------------------------------------ the contract


def _approved(client) -> tuple[dict, dict]:
    from app.routers import agreements as agreement_router

    class Stub:
        def generate(self, **kwargs):
            draft = AgreementDraft(
                title="Marketing site build",
                sections=[
                    {
                        "section_key": "SCOPE",
                        "title": "Scope",
                        "body": "A five page marketing site with a blog index and a contact form.",
                    }
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
            kwargs["validator"](draft)
            return draft

    original = agreement_router.get_ai_client
    agreement_router.get_ai_client = lambda **_: Stub()
    try:
        project = _project(client)
        version = client.post(
            f"/api/v1/projects/{project['id']}/agreement",
            json={"accepted_requirements": ["A five page site"]},
        ).json()
    finally:
        agreement_router.get_ai_client = original

    client.post(f"/api/v1/agreements/{version['id']}/send", json={})
    client.post(f"/api/v1/agreements/{version['id']}/approve", json={"role": "freelancer"})
    return project, version


def test_deleting_a_project_that_holds_a_signed_agreement_is_refused(client, db, ai):
    """A payment is protected; the contract that was signed deserves the same.
    Deleting it destroys the hash and both signatures while leaving approval
    audit rows pointing at nothing."""
    _signup(client)
    project, version = _approved(client)

    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(), base_url="https://testserver") as other:
        other.post(
            f"/api/v1/projects/{project['id']}/invitations", json={"email": CLIENT_EMAIL}
        )
        token = (
            client.post(
                f"/api/v1/projects/{project['id']}/invitations", json={"email": CLIENT_EMAIL}
            )
            .json()["invite_url"]
            .rsplit("/", 1)[-1]
        )
        other.post(f"/api/v1/invitations/{token}/accept")
        other.post(f"/api/v1/agreements/{version['id']}/approve", json={"role": "client"})

    response = client.delete(f"/api/v1/projects/{project['id']}")

    assert response.status_code == 409
    assert response.json()["code"] == "PROJECT_HAS_APPROVED_AGREEMENT"


def test_the_delete_audit_row_is_written_in_the_same_transaction(client, db, ai):
    _signup(client)
    project = _project(client)

    assert client.delete(f"/api/v1/projects/{project['id']}").status_code == 204

    row = db.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "PROJECT_DELETED")
    ).one()
    assert row.payload["project_id"] == project["id"]


def test_a_patch_cannot_null_a_required_field(client, db, ai):
    """`{"name": null}` used to reach the database and raise IntegrityError,
    which escaped as a 500 instead of a validation error."""
    _signup(client)
    project = _project(client)

    response = client.patch(f"/api/v1/projects/{project['id']}", json={"name": None})

    assert response.status_code == 422
