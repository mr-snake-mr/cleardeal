"""Merchant credentials: encrypted at rest, absent from every response.

The tests that matter here are the ones about what is *not* returned.
"""

from decimal import Decimal  # noqa: F401  (kept for parity with sibling tests)

import pytest
from sqlalchemy import delete, select

from app.config import settings
from app.crypto import CredentialUnreadable, EncryptionKeyMissing, decrypt, encrypt
from app.database import SessionLocal
from app.models import MerchantAccount, User

CLIENT_ID = "AaBbCcDdEeFfGgHh1234"
CLIENT_SECRET = "EL-SECRET-0123456789-abcdef"
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
    session = SessionLocal()
    try:
        session.execute(delete(MerchantAccount))
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


def _connect(client, **overrides):
    body = {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET} | overrides
    return client.put("/api/v1/merchants/paypal", json=body)


# ------------------------------------------------------------------- crypto


def test_round_trip_returns_the_original():
    assert decrypt(encrypt(CLIENT_SECRET)) == CLIENT_SECRET


def test_two_encryptions_of_the_same_value_differ():
    """Catches a fixed nonce, which would make the ciphertext a fingerprint."""
    assert encrypt(CLIENT_SECRET) != encrypt(CLIENT_SECRET)


def test_the_ciphertext_does_not_contain_the_plaintext():
    assert CLIENT_SECRET.encode() not in encrypt(CLIENT_SECRET)


def test_a_flipped_bit_is_rejected_not_silently_decrypted():
    blob = bytearray(encrypt(CLIENT_SECRET))
    blob[-1] ^= 0x01
    with pytest.raises(CredentialUnreadable):
        decrypt(bytes(blob))


def test_a_truncated_blob_is_rejected():
    with pytest.raises(CredentialUnreadable):
        decrypt(b"short")


def test_a_wrong_key_is_rejected(monkeypatch):
    blob = encrypt(CLIENT_SECRET)
    monkeypatch.setattr(settings, "credentials_encryption_key", "a-different-key")
    with pytest.raises(CredentialUnreadable):
        decrypt(blob)


def test_a_missing_key_raises_rather_than_storing_plaintext(monkeypatch):
    monkeypatch.setattr(settings, "credentials_encryption_key", "")
    with pytest.raises(EncryptionKeyMissing):
        encrypt(CLIENT_SECRET)


# -------------------------------------------------------------------- store


def test_connect_returns_201_and_no_secret_field(client):
    _signup(client)
    r = _connect(client)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "CONNECTED"
    assert not any("secret" in key or "client_id" in key for key in body)


def test_the_secret_is_stored_as_ciphertext(client, db):
    """Reads the raw column: a regression to plaintext fails here and nowhere else."""
    _signup(client)
    _connect(client)

    blob = db.execute(select(MerchantAccount.encrypted_client_secret)).scalar_one()

    assert CLIENT_SECRET.encode() not in blob
    assert decrypt(blob) == CLIENT_SECRET


def test_get_never_returns_the_secret(client):
    _signup(client)
    _connect(client)
    body = client.get("/api/v1/merchants/paypal").json()
    assert set(body) == {"status", "paypal_merchant_id", "connected_at"}
    assert CLIENT_SECRET not in str(body)


def test_reconnecting_replaces_rather_than_duplicates(client, db):
    _signup(client)
    _connect(client)
    _connect(client, client_secret="A-DIFFERENT-SECRET-000000")

    rows = db.execute(select(MerchantAccount)).scalars().all()

    assert len(rows) == 1
    assert decrypt(rows[0].encrypted_client_secret) == "A-DIFFERENT-SECRET-000000"


def test_get_before_connect_is_404(client):
    _signup(client)
    assert client.get("/api/v1/merchants/paypal").status_code == 404


def test_revoke_sets_status_and_is_idempotent(client):
    _signup(client)
    _connect(client)

    assert client.delete("/api/v1/merchants/paypal").status_code == 204
    assert client.get("/api/v1/merchants/paypal").json()["status"] == "REVOKED"
    assert client.delete("/api/v1/merchants/paypal").status_code == 204


def test_revoking_an_account_that_never_existed_is_204(client):
    _signup(client)
    assert client.delete("/api/v1/merchants/paypal").status_code == 204


def test_one_users_credentials_are_not_visible_to_another(client):
    _signup(client, "a@example.test")
    _connect(client)

    client.cookies.clear()
    _signup(client, "b@example.test")

    assert client.get("/api/v1/merchants/paypal").status_code == 404


def test_short_credentials_are_422(client):
    _signup(client)
    assert _connect(client, client_secret="short").status_code == 422


def test_undeclared_field_is_422(client):
    _signup(client)
    assert _connect(client, merchant_id="mine").status_code == 422


@pytest.mark.parametrize("method", ["put", "get", "delete"])
def test_every_merchant_route_requires_a_session(client, method):
    client.cookies.clear()
    response = getattr(client, method)("/api/v1/merchants/paypal")
    assert response.status_code == 401


def test_a_missing_key_returns_503_and_stores_nothing(client, db, monkeypatch):
    """Fail loudly: 503 with a code, no row, no traceback."""
    _signup(client)
    monkeypatch.setattr(settings, "credentials_encryption_key", "")

    r = _connect(client)

    assert r.status_code == 503
    assert r.json()["code"] == "ENCRYPTION_KEY_MISSING"
    assert db.execute(select(MerchantAccount)).scalars().all() == []
