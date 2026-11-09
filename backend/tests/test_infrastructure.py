"""Failure paths of the infrastructure layer.

The probe, the migration-state reader and the startup guard are where a silent
failure would be invisible in production, so each is exercised directly.

The migration-state tests use a private in-memory engine: they must not disturb
the migrated database the rest of the suite depends on.
"""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import migration_state, ping
from app.main import create_app


class _BrokenSession:
    """Minimal stand-in for a Session whose connection has died."""

    def execute(self, *_args, **_kwargs):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    def close(self) -> None:
        pass


def test_ping_returns_false_when_the_connection_dies():
    assert ping(_BrokenSession()) is False


def test_migration_state_is_pending_when_the_table_never_existed():
    """'Never migrated' is a deploy step, not an outage."""
    with Session(create_engine("sqlite://")) as db:
        assert migration_state(db) == "pending"


def test_migration_state_is_none_when_the_table_is_empty():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))

    with Session(engine) as db:
        assert migration_state(db) == "none"


def test_migration_state_returns_the_applied_revision():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
        conn.execute(text("INSERT INTO alembic_version VALUES ('a1b2c3')"))

    with Session(engine) as db:
        assert migration_state(db) == "a1b2c3"


def test_app_factory_rejects_wildcard_origin(monkeypatch):
    """'*' with credentials is silently broken in browsers; fail at startup."""
    monkeypatch.setattr(settings, "allowed_origins", ["*"])

    with pytest.raises(ValueError, match="explicit origins"):
        create_app()


def test_explicit_origins_are_accepted(monkeypatch):
    monkeypatch.setattr(settings, "allowed_origins", ["http://localhost:5173"])

    assert create_app() is not None
