"""Liveness/readiness probe.

Reports the PayPal environment so a demo cannot mistake sandbox for live
(docs/04 §4.4), and the applied Alembic revision so a reachable-but-unmigrated
database is visible.

The status code is 503 when the service is not ready. A probe that answers 200
while the database is unreachable is worse than no probe: an orchestrator keeps
routing traffic to a service that cannot serve it, and an operator reading
dashboards sees green during an outage.

`DbSession` is the Annotated form of `Depends(get_db)` — the current FastAPI
idiom, and it keeps `Depends` out of argument defaults (ruff B008).
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db, migration_state, ping

router = APIRouter()

DbSession = Annotated[Session, Depends(get_db)]


@router.get("/health")
def health(db: DbSession) -> JSONResponse:
    db_ok = ping(db)

    # "error" is reported as-is rather than folded into "pending": telling an
    # operator to run migrations during an outage sends them the wrong way.
    state = migration_state(db) if db_ok else "unknown"

    # "error" means the revision table exists but cannot be read. That is not
    # ready either — a service that cannot say which schema it is running is a
    # service whose queries may be about to fail.
    ready = db_ok and state not in {"pending", "error"}

    body = {
        "status": "ok" if ready else "degraded",
        "db": "ok" if db_ok else "unreachable",
        "migrations": state,
        "version": settings.app_version,
        "paypal_env": settings.paypal_env,
    }
    return JSONResponse(content=body, status_code=200 if ready else 503)
