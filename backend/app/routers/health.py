"""Liveness/readiness probe.

Reports the PayPal environment so a demo cannot mistake sandbox for live
(docs/04 §4.4), and the applied Alembic revision so a reachable-but-unmigrated
database is visible.

`DbSession` is the Annotated form of `Depends(get_db)` — the current FastAPI
idiom, and it keeps `Depends` out of argument defaults (ruff B008).
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db, migration_state, ping

router = APIRouter()

DbSession = Annotated[Session, Depends(get_db)]


@router.get("/health")
def health(db: DbSession) -> dict:
    db_ok = ping(db)
    return {
        "status": "ok" if db_ok else "degraded",
        "db": "ok" if db_ok else "unreachable",
        "migrations": migration_state(db) if db_ok else "unknown",
        "version": settings.app_version,
        "paypal_env": settings.paypal_env,
    }
