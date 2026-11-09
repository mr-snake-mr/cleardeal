"""The webhook route.

`POST /webhooks/paypal` is the only endpoint reachable without a session, so it
does nothing before the signature verifies: no parse, no row, no state change.

It lives outside `/api/v1` like `/health`: it is an integration surface PayPal
calls, not part of the client contract.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.services import webhooks as webhook_service

router = APIRouter(tags=["webhooks"])

DbSession = Annotated[Session, Depends(get_db)]

# Fetched once per certificate URL and kept: PayPal serves a stable cert, and a
# fetch per delivery would put the network on the hot path of every retry.
_CERT_CACHE: dict[str, bytes] = {}


def fetch_certificate(url: str) -> bytes:
    """A seam, like `paypal_client_for`: production fetches, tests inject.

    Only `https` is accepted. A `http` cert URL would let anyone on the path
    substitute the key that is supposed to prove the sender.
    """
    if url in _CERT_CACHE:
        return _CERT_CACHE[url]
    if not url.startswith("https://"):
        raise ValueError("certificate URL must be https")
    response = httpx.get(url, timeout=10.0)
    response.raise_for_status()
    _CERT_CACHE[url] = response.content
    return response.content


@router.post("/webhooks/paypal")
async def paypal_webhook(request: Request, db: DbSession) -> dict:
    # Raw bytes first. Anything else re-serialises the body and changes the
    # CRC32 the signature covers.
    raw = await request.body()

    header_values = {
        "id": request.headers.get("paypal-transmission-id"),
        "time": request.headers.get("paypal-transmission-time"),
        "sig": request.headers.get("paypal-transmission-sig"),
    }
    cert_url = request.headers.get("paypal-cert-url") or ""
    try:
        certificate = fetch_certificate(cert_url)
    except (ValueError, httpx.HTTPError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "CERTIFICATE_UNAVAILABLE",
                "message": "The signing certificate could not be retrieved.",
            },
        ) from None

    try:
        transmission_id = webhook_service.verify(
            raw=raw,
            headers=header_values,
            webhook_id=settings.paypal_webhook_id,
            cert_pem=certificate,
        )
    except webhook_service.SignatureInvalid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "SIGNATURE_INVALID",
                "message": "The webhook signature did not verify.",
                # The transmission id, not the body: enough to correlate with
                # PayPal's own logs, nothing that could be replayed.
                "details": {"transmission_id": header_values.get("id")},
            },
        ) from None

    try:
        event = webhook_service.load_event(raw)
        outcome = webhook_service.handle(db, event=event, now=datetime.now(UTC))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_PAYLOAD", "message": "The event was not usable."},
        ) from None

    return {"status": "ok", "outcome": outcome, "transmission_id": transmission_id}
