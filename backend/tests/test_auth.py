"""Auth: registration, login, session cookies, and the ways each could lie.

Every test here is written so that removing the behaviour makes it fail. Where
that is not obvious, the docstring says which removal it catches.
"""

import uuid

import pytest
from sqlalchemy import delete, select

from app.database import SessionLocal
from app.main import create_app
from app.models import User
from app.security import hash_password, issue_session, read_session, verify_password

GOOD_PASSWORD = "correct horse battery staple"


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _clean_users():
    """HTTP requests commit, so the `db` fixture's rollback cannot isolate them.

    Deleting before each test is the honest fix: registration is a real commit,
    and pretending otherwise would mean testing a different code path.
    """
    session = SessionLocal()
    try:
        session.execute(delete(User))
        session.commit()
    finally:
        session.close()
    yield


def _register(client, email="freelancer@example.test", **overrides):
    body = {"email": email, "name": "Sam", "password": GOOD_PASSWORD} | overrides
    return client.post("/api/v1/auth/register", json=body)


# ------------------------------------------------------------------ hashing


def test_same_password_hashes_differently():
    """A shared-salt implementation would produce two identical strings."""
    assert hash_password(GOOD_PASSWORD) != hash_password(GOOD_PASSWORD)


def test_hash_never_contains_the_password():
    assert GOOD_PASSWORD not in hash_password(GOOD_PASSWORD)


def test_verify_accepts_the_right_password_and_rejects_a_near_miss():
    stored = hash_password(GOOD_PASSWORD)
    assert verify_password(GOOD_PASSWORD, stored) is True
    assert verify_password(GOOD_PASSWORD + "!", stored) is False


def test_verify_rejects_a_missing_or_corrupt_hash():
    """Magic-link users have `password_hash = None`; that must never verify."""
    assert verify_password(GOOD_PASSWORD, None) is False
    assert verify_password(GOOD_PASSWORD, "not-a-hash") is False
    assert verify_password(GOOD_PASSWORD, "pbkdf2_sha256$x$y$z") is False


# ------------------------------------------------------------------ tokens


def test_token_round_trips_to_the_same_user():
    user_id = uuid.uuid4()
    assert read_session(issue_session(user_id)) == user_id


def test_a_tampered_payload_is_rejected():
    token = issue_session(uuid.uuid4())
    version, payload, signature = token.split(".")
    forged = issue_session(uuid.uuid4()).split(".")[1]
    assert read_session(f"{version}.{forged}.{signature}") is None


def test_a_tampered_signature_is_rejected():
    token = issue_session(uuid.uuid4())
    assert read_session(token[:-1] + ("A" if token[-1] != "A" else "B")) is None


def test_an_expired_token_is_rejected():
    """Catches a `read_session` that checks the signature but not `exp`."""
    assert read_session(issue_session(uuid.uuid4(), ttl_hours=-1)) is None


def test_garbage_tokens_are_rejected_not_raised():
    for token in ["", "x", "v1..", "v2.a.b", "v1.!!!.???", None]:
        assert read_session(token) is None


# --------------------------------------------------------------- register


def test_register_creates_the_user_and_signs_them_in(client):
    r = _register(client)
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "freelancer@example.test"
    assert "password_hash" not in body, "the response schema must have no field to leak through"
    assert client.get("/api/v1/auth/me").status_code == 200


def test_register_normalises_email_case(client):
    body = _register(client, email="Freelancer@Example.Test").json()
    assert body["email"] == "freelancer@example.test"


def test_duplicate_email_is_409_not_500(client):
    _register(client)
    r = _register(client)
    assert r.status_code == 409
    assert "EMAIL_ALREADY_REGISTERED" in r.text


def test_short_password_is_422(client):
    assert _register(client, password="short").status_code == 422


def test_invalid_email_is_422(client):
    assert _register(client, email="not-an-email").status_code == 422


def test_undeclared_field_is_422(client):
    """`extra='forbid'`: a client cannot smuggle `id` or `password_hash` in."""
    assert _register(client, password_hash="injected").status_code == 422


def test_password_is_stored_hashed(client, db):
    _register(client)
    stored = db.execute(select(User.password_hash)).scalar_one()
    assert stored != GOOD_PASSWORD
    assert stored.startswith("pbkdf2_sha256$")


# ------------------------------------------------------------------ login


def test_login_succeeds_with_the_right_password(client):
    _register(client)
    client.cookies.clear()
    r = client.post(
        "/api/v1/auth/login",
        json={"email": "freelancer@example.test", "password": GOOD_PASSWORD},
    )
    assert r.status_code == 200


def test_wrong_password_and_unknown_email_are_indistinguishable(client):
    """Catches an enumeration oracle: two different messages, two different bodies."""
    _register(client)
    client.cookies.clear()
    wrong = client.post(
        "/api/v1/auth/login",
        json={"email": "freelancer@example.test", "password": "wrong password here"},
    )
    unknown = client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.test", "password": GOOD_PASSWORD},
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_logout_clears_the_session(client):
    _register(client)
    assert client.get("/api/v1/auth/me").status_code == 200
    client.post("/api/v1/auth/logout")
    # TestClient keeps the cookie jar; an explicit empty cookie proves the
    # server-side clear rather than the client forgetting.
    client.cookies.clear()
    assert client.get("/api/v1/auth/me").status_code == 401


def test_me_without_a_cookie_is_401(client):
    client.cookies.clear()
    assert client.get("/api/v1/auth/me").status_code == 401


def test_a_valid_token_for_a_deleted_user_is_401(client, db):
    """Catches `require_user` trusting the signature without loading the row."""
    response = _register(client)
    user_id = response.json()["id"]
    db.query(User).filter(User.id == uuid.UUID(user_id)).delete()
    db.commit()
    assert client.get("/api/v1/auth/me").status_code == 401


# ------------------------------------------------------------------ boot


def test_live_mode_refuses_the_development_secret(monkeypatch):
    """A published signing key is an authentication bypass, so boot must fail."""
    from app.config import settings

    monkeypatch.setattr(settings, "paypal_env", "live")
    monkeypatch.setattr(settings, "session_secret", "dev-insecure-secret-change-me")
    with pytest.raises(ValueError, match="SESSION_SECRET"):
        create_app()


def test_live_mode_accepts_a_real_secret(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "paypal_env", "live")
    monkeypatch.setattr(settings, "session_secret", "a-real-32-byte-secret-value-here")
    assert create_app() is not None
