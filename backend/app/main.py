"""App factory.

Deliberate omission: there is no `Base.metadata.create_all()` here. Schema
changes go through Alembic only (docs/07 Â§6). create_all hides drift and cannot
express the constraints the schema depends on.
"""

import pathlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.config import settings
from app.database import SessionLocal, engine
from app.errors import install_error_handlers
from app.logging_config import install_logging
from app.routers import (
    agreements,
    auth,
    changes,
    health,
    invitations,
    merchants,
    payments,
    projects,
    webhooks,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    engine.dispose()


def create_app() -> FastAPI:
    # A wildcard origin with credentials is rejected by browsers and silently
    # echoed by Starlette, which looks like it works until it doesn't.
    if "*" in settings.allowed_origins:
        raise ValueError(
            "ALLOWED_ORIGINS must list explicit origins; '*' is incompatible "
            "with credentialed requests."
        )

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        # Always False: with debug on, Starlette renders a traceback page and
        # our sanitised 500 envelope is bypassed. Tracebacks belong in logs.
        debug=False,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        # Authorization and Idempotency-Key are pre-declared: both arrive with
        # auth and payments, and a missing header fails the browser preflight in
        # a way that looks like a server bug.
        allow_headers=["Content-Type", "Authorization", "Idempotency-Key"],
    )

    # A published signing key is an authentication bypass, and it is one in the
    # sandbox demo too â€” the token is forgeable regardless of which PayPal host
    # the app talks to. So the guard is on `debug`, not on `paypal_env`: any
    # deployment that is not explicitly a debug server must carry a real secret.
    if not settings.debug and settings.session_secret.startswith("dev-insecure"):
        raise ValueError(
            "SESSION_SECRET must be set to a real secret in live mode; the "
            "development default is public."
        )

    install_logging()

    # docs/10 Â§4: "startup fails if the key is absent while a merchant row
    # exists". Without this the app boots, reports CONNECTED, and only fails on
    # the first payment â€” the worst possible moment to discover it.
    with SessionLocal() as probe:
        from sqlalchemy import func, select

        from app.models import MerchantAccount

        try:
            merchant_rows = probe.execute(
                select(func.count()).select_from(MerchantAccount)
            ).scalar_one()
        except Exception:  # noqa: BLE001 - an unmigrated DB is reported by /health
            merchant_rows = 0

    if merchant_rows and not settings.credentials_encryption_key:
        raise ValueError(
            "CREDENTIALS_ENCRYPTION_KEY is not set but merchant credentials "
            "exist. Refusing to start: every stored credential would be "
            "unreadable, and a first payment would fail instead of a boot."
        )

    # Infra probe, deliberately outside /api/v1 so it never becomes part of the
    # public contract.
    # The demo page. Served from the app on purpose: no build step, no second
    # process, and no CORS to get wrong on the morning of a demo.
    page = pathlib.Path(__file__).parent / "static" / "index.html"

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(page)

    app.include_router(health.router, tags=["infra"])
    app.include_router(webhooks.router)

    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(projects.router, prefix="/api/v1")
    app.include_router(merchants.router, prefix="/api/v1")
    app.include_router(agreements.router, prefix="/api/v1")
    app.include_router(payments.router, prefix="/api/v1")
    app.include_router(changes.router, prefix="/api/v1")
    app.include_router(invitations.router, prefix="/api/v1")

    # Installed after the routers so the handlers are the outermost layer and
    # catch failures raised inside route code as well as during validation.
    install_error_handlers(app)

    return app


app = create_app()
