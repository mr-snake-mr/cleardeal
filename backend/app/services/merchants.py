"""Per-tenant PayPal credentials (Model B, docs/04 §2).

What this does: stores the pair encrypted, keeps one row per user, and never
hands the plaintext back except to the PayPal client, which is the only caller
of `credentials_for`.

What this does **not** do yet: prove the credentials work. That needs the PayPal
token endpoint, which is block 2.8. Until then `status` is `CONNECTED` on trust
and `paypal_merchant_id` stays `NULL`; an invalid pair surfaces on the first
order rather than at connect time. docs/08 §9 says so explicitly rather than
implying a verification that is not there.

`shortcut: credentials are accepted unverified until the PayPal client exists,
add token verification in block 2.8.`
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crypto import decrypt, encrypt
from app.models import MerchantAccount


def connect(
    db: Session, *, user_id: uuid.UUID, client_id: str, client_secret: str
) -> MerchantAccount:
    """One row per user: reconnecting replaces the pair rather than stacking."""
    row = db.execute(
        select(MerchantAccount).where(MerchantAccount.user_id == user_id)
    ).scalar_one_or_none()

    if row is None:
        row = MerchantAccount(
            user_id=user_id,
            encrypted_client_id=encrypt(client_id),
            encrypted_client_secret=encrypt(client_secret),
        )
        db.add(row)
    else:
        row.encrypted_client_id = encrypt(client_id)
        row.encrypted_client_secret = encrypt(client_secret)
        # A revoked account becomes usable again by reconnecting; that is the
        # point of the endpoint.
        row.status = "CONNECTED"

    db.commit()
    db.refresh(row)
    return row


def get(db: Session, *, user_id: uuid.UUID) -> MerchantAccount | None:
    return db.execute(
        select(MerchantAccount).where(MerchantAccount.user_id == user_id)
    ).scalar_one_or_none()


def credentials_for(db: Session, *, user_id: uuid.UUID) -> tuple[str, str] | None:
    """Plaintext pair for the PayPal client. The only reader of the ciphertext."""
    row = get(db, user_id=user_id)
    if row is None or row.status != "CONNECTED":
        return None
    return decrypt(row.encrypted_client_id), decrypt(row.encrypted_client_secret)


def revoke(db: Session, *, user_id: uuid.UUID) -> bool:
    """Status, not deletion: the row is evidence that credentials were held.

    Payments store `merchant_user_id`, not the credential, so revoking cannot
    orphan a payment — but keeping the row means the audit trail can still say
    when a merchant was connected and when they left.
    """
    row = get(db, user_id=user_id)
    if row is None:
        return False
    row.status = "REVOKED"
    db.commit()
    return True
