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


# The certificate URL arrives in the request. Trusting it is the whole game: an
# attacker who can name the key that verifies their own signature has not been
# verified at all. So the host must be PayPal's, not merely "https".
_ALLOWED_CERT_HOSTS = (".paypal.com", ".paypalobjects.com")


def _is_paypal_host(url: str) -> bool:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    return any(
        host == allowed.lstrip(".") or host.endswith(allowed)
        for allowed in _ALLOWED_CERT_HOSTS
    )


def fetch_certificate(url: str) -> bytes:
    """A seam, like `paypal_client_for`: production fetches, tests inject.

    The host allow-list is the security property. `https` alone proves the
    transport, not the sender; `paypal-cert-url` is attacker-controlled input and
    must not be able to name an arbitrary signing key.
    """
    if not _is_paypal_host(url):
        raise ValueError("certificate URL is not a PayPal host")
    if url in _CERT_CACHE:
        return _CERT_CACHE[url]
    content = _download_certificate(url)
    _CERT_CACHE[url] = content
    return content


def _download_certificate(url: str) -> bytes:
    """The network call, alone, so tests can replace it without also replacing
    the host allow-list above — the check is the property, not the fetch."""
    response = httpx.get(url, timeout=10.0)
    response.raise_for_status()
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
            now=datetime.now(UTC),
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
    except ValueError:
        # Verified but unreadable: 200, because a 400 makes PayPal resend the
        # same unreadable body 25 times (docs/08 §8: "200 always once verified").
        return {"status": "ok", "outcome": "invalid_payload"}

    outcome = webhook_service.handle(db, event=event, now=datetime.now(UTC))
    return {"status": "ok", "outcome": outcome, "transmission_id": transmission_id}
