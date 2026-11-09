"""App factory.

Deliberate omission: there is no `Base.metadata.create_all()` here. Schema
changes go through Alembic only (docs/07 §6). create_all hides drift and cannot
express the constraints the schema depends on.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import engine
from app.routers import auth, health


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
        debug=settings.debug,
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
    # sandbox demo too — the token is forgeable regardless of which PayPal host
    # the app talks to. So the guard is on `debug`, not on `paypal_env`: any
    # deployment that is not explicitly a debug server must carry a real secret.
    if not settings.debug and settings.session_secret.startswith("dev-insecure"):
        raise ValueError(
            "SESSION_SECRET must be set to a real secret in live mode; the "
            "development default is public."
        )

    # Infra probe, deliberately outside /api/v1 so it never becomes part of the
    # public contract.
    app.include_router(health.router, tags=["infra"])

    app.include_router(auth.router, prefix="/api/v1")

    return app


app = create_app()
