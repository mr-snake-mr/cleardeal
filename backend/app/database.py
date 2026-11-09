"""Engine and session management.

One engine per process. Sessions are request-scoped and always closed by the
dependency, so a failed request cannot leak a connection.

SQLite detail that matters: SQLite ignores foreign keys unless
`PRAGMA foreign_keys=ON` runs **per connection**. Without it every ON DELETE
policy in the schema is decorative and a green test suite proves nothing about
referential integrity. The listener below turns them on for every connection,
including the test engine.
"""

import logging
import sqlite3
from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Declarative base. Models are imported by Alembic, not by this module."""


def _engine_kwargs(url: str) -> dict:
    # SQLite needs check_same_thread=False because FastAPI runs sync handlers in
    # a threadpool. Postgres needs no such concession.
    if url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True}


engine = create_engine(settings.database_url, **_engine_kwargs(settings.database_url))


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    """SQLite defaults to FKs off. Enforce them on every connection."""
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: yields a session, always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ping(db: Session) -> bool:
    """Cheap liveness probe. Catches SQLAlchemyError only, so a programming bug
    is not disguised as an outage, and logs the failure rather than swallowing
    it."""
    try:
        db.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError:
        logger.warning("database liveness probe failed", exc_info=True)
        return False


def migration_state(db: Session) -> str:
    """Which Alembic revision is applied.

      "<revision>" — the applied revision
      "none"       — alembic_version exists but holds no row
      "pending"    — the table is absent, i.e. migrations never ran
      "error"      — the database could not be read at all

    There is no create_all() anywhere, so a reachable database with no tables is
    not healthy. Distinguishing "never migrated" from "cannot read" matters: the
    first is a deploy step, the second an outage, and reporting both as "pending"
    sends the operator to the wrong fix.
    """
    try:
        revision = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except SQLAlchemyError as exc:
        logger.warning("could not read alembic_version", exc_info=True)
        return "pending" if _is_missing_table(exc) else "error"
    return revision or "none"


def _is_missing_table(exc: SQLAlchemyError) -> bool:
    """True when the failure is specifically 'no such table'."""
    message = str(getattr(exc, "orig", exc)).lower()
    return "no such table" in message or "does not exist" in message
