"""Client access: a magic link, and two principals who cannot be one person.

This is the block that deletes A-15. Until now `role` was whatever the request
body said, and the only caller who could reach `approve` was the owner â€” so one
person could sign twice and the audit row said "the client approved".

Three properties make that impossible:

- the token is a bearer credential: only its hash is stored, it expires, and it
  is single use;
- accepting it creates (or reuses) a user for the invited email and issues a
  session, so the client is a principal with an id of its own;
- `approve` binds a role to that id: the owner may only sign as freelancer, and
  only an invited client may sign as client.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

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


def _project_and_version(client):
    project = client.post(
        "/api/v1/projects",
        json={
            "name": "Marketing site",
            "client_email": CLIENT_EMAIL,
            "brief": "A five page marketing site with a blog index and a contact form.",
            "budget_amount": "300.00",
        },
    )
    assert project.status_code == 201, project.text
    project = project.json()
    version = client.post(
        f"/api/v1/projects/{project['id']}/agreement",
        json={"accepted_requirements": ["A five page site"]},
    )
    assert version.status_code == 201, version.text
    return project, version.json()


def _invite(client, project_id):
    return client.post(
        f"/api/v1/projects/{project_id}/invitations", json={"email": CLIENT_EMAIL}
    )


def _accept(client, url):
    token = url.rsplit("/", 1)[-1]
    return client.post(f"/api/v1/invitations/{token}/accept")


# ------------------------------------------------------------------ the token


def test_the_invitation_stores_a_hash_and_returns_the_token_once(client, db, ai):
    _signup(client)
    project, _ = _project_and_version(client)

    response = _invite(client, project["id"])

    assert response.status_code == 201, response.text
    body = response.json()
    token = body["invite_url"].rsplit("/", 1)[-1]
    assert len(token) >= 32
    assert body["expires_at"]

    stored = db.scalars(select(Invitation)).one()
    assert stored.token_hash == hashlib.sha256(token.encode()).hexdigest()
    # The raw token must not be recoverable from the database.
    assert token not in stored.token_hash


def test_the_invite_link_points_at_the_host_that_served_it(client, db, ai):
    """A human pastes this link wherever the app is actually running.

    It was built from `settings.allowed_origins[0]`, which defaults to
    `http://localhost:5173` â€” an origin that only exists on a developer's
    laptop. On the deployed host the client clicked it and got
    ERR_CONNECTION_REFUSED, which blocks approval, which blocks payment and
    the scope check. The link must be relative to the request that asked for it.
    """
    _signup(client)
    project, _ = _project_and_version(client)

    url = _invite(client, project["id"]).json()["invite_url"]

    assert "localhost:5173" not in url
    assert url.startswith("https://testserver/"), url


def test_accepting_a_token_creates_the_client_and_uses_it_up(client, db, ai):
    _signup(client)
    project, _ = _project_and_version(client)
    url = _invite(client, project["id"]).json()["invite_url"]

    response = _accept(client, url)

    assert response.status_code == 200, response.text
    assert response.json()["email"] == CLIENT_EMAIL

    db.expire_all()
    invited_user = db.scalars(select(User).where(User.email == CLIENT_EMAIL)).one()
    assert invited_user.password_hash is None, "a magic-link user has no password"
    assert db.scalars(select(Invitation)).one().used_at is not None


def test_a_token_cannot_be_used_twice(client, db, ai):
    _signup(client)
    project, _ = _project_and_version(client)
    url = _invite(client, project["id"]).json()["invite_url"]
    _accept(client, url)

    again = _accept(client, url)

    assert again.status_code == 409
    assert again.json()["code"] == "INVITATION_USED"


def test_an_expired_token_is_refused(client, db, ai):
    _signup(client)
    project, _ = _project_and_version(client)
    url = _invite(client, project["id"]).json()["invite_url"]

    invitation = db.scalars(select(Invitation)).one()
    invitation.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()

    response = _accept(client, url)

    assert response.status_code == 409
    assert response.json()["code"] == "INVITATION_EXPIRED"


def test_an_unknown_token_is_404_not_a_hint(client, db, ai):
    """404 everywhere: an `invalid token` message that differs from `expired`
    tells a scanner which guesses were close."""
    assert (
        client.post("/api/v1/invitations/not-a-real-token-at-all/accept").status_code
        == 404
    )


def test_only_the_owner_can_invite(client, db, ai):
    _signup(client, email="owner@example.test")
    project, _ = _project_and_version(client)

    _signup(client, email="intruder@example.test")

    assert _invite(client, project["id"]).status_code == 404


# ------------------------------------------------------- the two principals


def test_two_distinct_principals_sign_and_the_agreement_is_approved(client, db, ai):
    """The replacement for A-15, and the assertion that ends the caveat."""
    _signup(client)
    project, version = _project_and_version(client)
    client.post(f"/api/v1/agreements/{version['id']}/send", json={})

    first = client.post(
        f"/api/v1/agreements/{version['id']}/approve", json={"role": "freelancer"}
    )
    assert first.status_code == 200, first.text

    url = _invite(client, project["id"]).json()["invite_url"]
    assert _accept(client, url).status_code == 200

    second = client.post(f"/api/v1/agreements/{version['id']}/approve", json={"role": "client"})

    assert second.status_code == 200, second.text
    assert second.json()["status"] == "APPROVED"

    rows = db.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "AGREEMENT_APPROVED")
    ).all()
    assert len({row.actor_id for row in rows}) == 2, "two roles, two different actors"


def test_the_owner_cannot_sign_as_the_client(client, db, ai):
    """The exact hole A-15 described: one person ticking both boxes."""
    _signup(client)
    _, version = _project_and_version(client)
    client.post(f"/api/v1/agreements/{version['id']}/send", json={})
    client.post(f"/api/v1/agreements/{version['id']}/approve", json={"role": "freelancer"})

    response = client.post(
        f"/api/v1/agreements/{version['id']}/approve", json={"role": "client"}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "ROLE_NOT_YOURS"
    assert response.json()["message"].find("client") != -1


def test_a_client_cannot_sign_as_the_freelancer(client, db, ai):
    _signup(client)
    project, version = _project_and_version(client)
    client.post(f"/api/v1/agreements/{version['id']}/send", json={})
    url = _invite(client, project["id"]).json()["invite_url"]
    _accept(client, url)

    response = client.post(
        f"/api/v1/agreements/{version['id']}/approve", json={"role": "freelancer"}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "ROLE_NOT_YOURS"


def test_an_uninvited_account_cannot_sign_as_the_client(client, db, ai):
    """A session is not authorisation: the client role needs an invitation for
    *this* project, accepted by *this* account."""
    _signup(client, email="owner@example.test")
    project, version = _project_and_version(client)
    client.post(f"/api/v1/agreements/{version['id']}/send", json={})

    _invite(client, project["id"])
    # A different account that never accepted anything.
    _signup(client, email="someone-else@example.test")

    response = client.post(
        f"/api/v1/agreements/{version['id']}/approve", json={"role": "client"}
    )

    assert response.status_code == 404, "not their project, so not found at all"


def test_the_emailed_link_is_served_at_the_root(client, db, ai):
    """The URL in the email must resolve on the server that sent it.

    The handler lived under /api/v1 while the link said /invite/{token}, so a
    real client clicked it and got {"code": "NOT_FOUND"}. The link is pasted out
    of an email; it has to be the path the browser is actually given.
    """
    _signup(client)
    project, version = _project_and_version(client)
    url = _invite(client, project["id"]).json()["invite_url"]
    path = url.split("testserver", 1)[-1]

    response = client.get(path, follow_redirects=False)

    assert response.status_code == 303, response.text
    location = response.headers["location"]
    assert location.startswith("/?invite=accepted"), location
    assert f"v={version['id']}" in location, (
        "the page has no other way to learn which agreement the client must sign"
    )
