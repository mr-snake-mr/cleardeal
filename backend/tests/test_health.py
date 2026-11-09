"""Tests that fail when the behaviour is removed.

Each targets a specific way the endpoint could silently lie: a hardcoded "ok", a
hardcoded environment, or a database reported healthy while unmigrated.
"""

from app import routers
from app.config import settings


def test_health_reports_ok_and_db_reachable(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"


def test_health_reports_degraded_when_db_ping_fails(client, monkeypatch):
    """If ping() were deleted and "ok" hardcoded, this test fails."""
    monkeypatch.setattr(routers.health, "ping", lambda _db: False)

    r = client.get("/health")

    assert r.status_code == 503, "a not-ready service must not answer 200"
    body = r.json()
    assert body["status"] == "degraded"
    assert body["db"] == "unreachable"
    assert body["migrations"] == "unknown"


def test_health_reports_503_when_migrations_are_pending(client, monkeypatch):
    """A reachable but unmigrated database is not ready — it will fail every
    request that touches a table."""
    monkeypatch.setattr(routers.health, "migration_state", lambda _db: "pending")

    r = client.get("/health")

    assert r.status_code == 503
    assert r.json()["migrations"] == "pending"


def test_health_reads_paypal_env_from_settings(client, monkeypatch):
    """If the environment were hardcoded to "sandbox", this test fails."""
    monkeypatch.setattr(settings, "paypal_env", "live")

    assert client.get("/health").json()["paypal_env"] == "live"


def test_health_exposes_migration_state(client):
    """Contract check only — the values themselves are proven in
    test_infrastructure.py against a real alembic_version table."""
    value = client.get("/health").json()["migrations"]
    assert isinstance(value, str) and value
    assert value in {"pending", "none", "error"} or value.isalnum()


def test_health_is_not_under_api_prefix(client):
    """The probe is infrastructure, not part of the public API contract."""
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/health").status_code == 404
