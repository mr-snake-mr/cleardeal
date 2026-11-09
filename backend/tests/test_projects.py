"""Projects: creation, validation, and the two cross-tenant failures.

Each test states the behaviour that would have to be deleted for it to pass.
"""

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import delete, select, text

from app.database import SessionLocal
from app.models import Project, User

GOOD = {
    "name": "Marketing site",
    "client_email": "client@example.test",
    "brief": "A five page marketing site with a blog and a contact form.",
    "budget_amount": "300.00",
}

PASSWORD = "correct horse battery staple"


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _clean():
    """HTTP requests commit, so isolation has to be explicit.

    Projects must go before users: `owner_id` is ON DELETE RESTRICT.
    """
    session = SessionLocal()
    try:
        session.execute(delete(Project))
        session.execute(delete(User))
        session.commit()
    finally:
        session.close()
    yield


def _signup(client, email="freelancer@example.test"):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Sam", "password": PASSWORD},
    )
    assert response.status_code == 201, response.text
    return response


def _create(client, **overrides):
    return client.post("/api/v1/projects", json=GOOD | overrides)


# ---------------------------------------------------------------- creation


def test_create_returns_201_the_project_and_a_draft_status(client):
    _signup(client)
    r = _create(client)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "DRAFT"
    assert body["currency"] == "USD"
    assert body["id"]


def test_budget_is_serialised_as_a_fixed_scale_string(client):
    """Catches a schema that emits 300.0 or a float: money on the wire is a string."""
    _signup(client)
    assert _create(client).json()["budget_amount"] == "300.00"


def test_budget_is_stored_exactly(client, db):
    """Catches a regression from integer cents back to float."""
    _signup(client)
    project_id = _create(client, budget_amount="1234.57").json()["id"]
    raw = db.execute(
        text("SELECT budget_amount FROM projects WHERE id = :id"),
        {"id": project_id.replace("-", "")},
    ).scalar()
    assert raw == 123457


@pytest.mark.parametrize("budget", ["0.00", "-5.00"])
def test_budget_must_be_positive(client, budget):
    _signup(client)
    assert _create(client, budget_amount=budget).status_code == 422


def test_budget_rejects_sub_cent_precision(client):
    """300.001 is not money. Rounding it silently is how a cent goes missing."""
    _signup(client)
    assert _create(client, budget_amount="300.001").status_code == 422


def test_brief_must_be_long_enough_to_analyse(client):
    _signup(client)
    assert _create(client, brief="too short").status_code == 422


def test_deadline_in_the_past_is_422(client):
    _signup(client)
    past = (date.today() - timedelta(days=1)).isoformat()
    assert _create(client, deadline=past).status_code == 422


def test_deadline_today_is_allowed(client):
    """Due today is not overdue."""
    _signup(client)
    assert _create(client, deadline=date.today().isoformat()).status_code == 201


def test_unknown_field_is_rejected(client):
    _signup(client)
    assert _create(client, status="APPROVED").status_code == 422


def test_invalid_client_email_is_422(client):
    _signup(client)
    assert _create(client, client_email="not-an-email").status_code == 422


def test_unauthenticated_create_is_401(client):
    client.cookies.clear()
    assert _create(client).status_code == 401


# ----------------------------------------------------------------- isolation


def test_list_returns_only_the_callers_projects(client):
    """Catches a `select(Project)` with no owner filter."""
    _signup(client, "a@example.test")
    _create(client, name="A project")

    client.cookies.clear()
    _signup(client, "b@example.test")

    body = client.get("/api/v1/projects").json()
    assert body["items"] == []
    assert body["total"] == 0


def test_list_orders_newest_first(client, db):
    """`created_at` is controlled here on purpose.

    Two projects created in the same second tie on `created_at`, and the id
    tiebreak makes their order arbitrary-but-stable. Asserting recency with
    real timestamps would test the clock, not the query.
    """
    _signup(client)
    first_id = uuid.UUID(_create(client, name="first").json()["id"])
    second_id = uuid.UUID(_create(client, name="second").json()["id"])
    db.execute(
        text("UPDATE projects SET created_at = :ts WHERE id = :id"),
        {"ts": "2026-01-01 10:00:00", "id": first_id.hex},
    )
    db.execute(
        text("UPDATE projects SET created_at = :ts WHERE id = :id"),
        {"ts": "2026-01-02 10:00:00", "id": second_id.hex},
    )
    db.commit()

    names = [p["name"] for p in client.get("/api/v1/projects").json()["items"]]
    assert names == ["second", "first"]


def test_another_users_project_is_404_not_403(client):
    """403 would confirm the id exists; the ids are UUIDs so they are not enumerable."""
    _signup(client, "a@example.test")
    project_id = _create(client).json()["id"]

    client.cookies.clear()
    _signup(client, "b@example.test")

    r = client.get(f"/api/v1/projects/{project_id}")
    assert r.status_code == 404
    assert "NOT_FOUND" in r.text


def test_own_project_is_readable(client):
    _signup(client)
    project_id = _create(client).json()["id"]
    r = client.get(f"/api/v1/projects/{project_id}")
    assert r.status_code == 200
    assert r.json()["id"] == project_id


def test_unknown_project_id_is_404(client):
    _signup(client)
    assert client.get("/api/v1/projects/00000000-0000-4000-8000-000000000000").status_code == 404


def test_malformed_project_id_is_422(client):
    """A non-UUID path segment never reaches the database."""
    _signup(client)
    assert client.get("/api/v1/projects/not-a-uuid").status_code == 422


def test_creation_attributes_the_project_to_the_session_user(client, db):
    """Catches an owner_id taken from the request body instead of the session."""
    user_id = uuid.UUID(_signup(client).json()["id"])
    project_id = uuid.UUID(_create(client).json()["id"])

    owner_id = db.execute(select(Project.owner_id).where(Project.id == project_id)).scalar_one()

    assert owner_id == user_id
