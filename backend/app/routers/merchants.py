"""Merchant credential routes.

The response schema has no secret field, so there is nothing to redact and no
future endpoint to remember to redact. The exception path proves it: a missing
encryption key is a `503` telling the operator what to set, not a `500` with a
traceback and not a silent plaintext write.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.crypto import EncryptionKeyMissing
from app.database import get_db
from app.routers.auth import CurrentUser
from app.schemas.merchants import MerchantConnect, MerchantOut
from app.services import merchants as merchant_service

router = APIRouter(prefix="/merchants", tags=["merchants"])

DbSession = Annotated[Session, Depends(get_db)]


def _key_missing() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "ENCRYPTION_KEY_MISSING",
            "message": "Credential storage is not configured on this server.",
        },
    )


@router.put("/paypal", response_model=MerchantOut, status_code=status.HTTP_201_CREATED)
def connect_paypal(
    payload: MerchantConnect, user: CurrentUser, db: DbSession
) -> MerchantOut:
    try:
        row = merchant_service.connect(
            db, user_id=user.id, client_id=payload.client_id, client_secret=payload.client_secret
        )
    except EncryptionKeyMissing:
        raise _key_missing() from None
    return MerchantOut.model_validate(row)


@router.get("/paypal", response_model=MerchantOut)
def get_paypal(user: CurrentUser, db: DbSession) -> MerchantOut:
    row = merchant_service.get(db, user_id=user.id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "No PayPal account is connected."},
        )
    return MerchantOut.model_validate(row)


@router.delete("/paypal", status_code=status.HTTP_204_NO_CONTENT)
def revoke_paypal(user: CurrentUser, db: DbSession) -> Response:
    merchant_service.revoke(db, user_id=user.id)
    # Idempotent: revoking an account that was never connected is not an error,
    # because the caller's intent — "no credentials stored" — is already true.
    return Response(status_code=status.HTTP_204_NO_CONTENT)
