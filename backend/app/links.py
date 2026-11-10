"""Absolute URLs the app hands to a human.

The rule: build links from the request that produced them, or from a value the
deployment sets on purpose. Never from `allowed_origins` — that is a CORS list,
its first entry defaults to `http://localhost:5173`, and an origin that only
exists on a laptop is exactly how a deployed invite link ends up refusing to
connect (docs/08 §9).
"""

from __future__ import annotations

from fastapi import Request

from app.config import settings


def base_url(request: Request) -> str:
    """`https://host` for the host that served this request."""
    configured = settings.public_base_url.strip().rstrip("/")
    if configured:
        # A proxy in front of uvicorn is not always trusted to forward the
        # scheme and host, so a deployment may state its own origin outright.
        return configured
    return str(request.base_url).rstrip("/")
