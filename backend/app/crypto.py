"""AES-GCM for merchant credentials at rest.

Why GCM and not CBC: GCM authenticates. A CBC blob that someone flips a bit in
decrypts to garbage that looks like a wrong key; a GCM blob raises. For a value
that becomes an API credential, "tampered" must be distinguishable from "wrong".

**The ciphertext is bound to its owner.** `aad` is the owning user id, so a blob
copied from one row into another fails to decrypt instead of silently becoming
someone else's PayPal identity. Without it, any primitive that can write a row —
a restore, a bad migration, an unrelated injection bug — is an escalation.

Key handling: 32 bytes, accepted as 64 hex characters or 44-character base64.
Anything else raises. `sha256(...)` over an arbitrary passphrase would produce a
256-bit-shaped key with however few bits the passphrase had, and nothing would
warn — so a malformed key is rejected instead of quietly weakened.
"""

from __future__ import annotations

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings

NONCE_BYTES = 12
KEY_BYTES = 32


class EncryptionKeyMissing(RuntimeError):
    """No key configured. Not a 500: the operator has not finished setting up."""


class EncryptionKeyInvalid(RuntimeError):
    """A key is configured but is not 32 bytes. Weakened silently is worse."""


class CredentialUnreadable(RuntimeError):
    """Wrong key, wrong owner, or altered ciphertext. All mean "do not use this"."""


def _key() -> bytes:
    raw = (settings.credentials_encryption_key or "").strip()
    if not raw:
        raise EncryptionKeyMissing(
            "CREDENTIALS_ENCRYPTION_KEY is not set; merchant credentials cannot "
            "be stored or read."
        )

    key: bytes | None = None
    if len(raw) == KEY_BYTES * 2:
        try:
            key = bytes.fromhex(raw)
        except ValueError:
            key = None
    elif len(raw) == 44:
        try:
            key = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError):
            key = None

    if key is None or len(key) != KEY_BYTES:
        # The message names the shape, never the value.
        raise EncryptionKeyInvalid(
            "CREDENTIALS_ENCRYPTION_KEY must be 32 bytes: 64 hex characters or "
            "44-character base64."
        )
    return key


def encrypt(plaintext: str, *, aad: bytes | None = None) -> bytes:
    """`nonce || ciphertext||tag`. A fresh nonce per call, never reused."""
    nonce = os.urandom(NONCE_BYTES)
    return nonce + AESGCM(_key()).encrypt(nonce, plaintext.encode("utf-8"), aad)


def decrypt(blob: bytes, *, aad: bytes | None = None) -> str:
    if len(blob) <= NONCE_BYTES:
        raise CredentialUnreadable("ciphertext is too short to contain a nonce")
    nonce, ciphertext = blob[:NONCE_BYTES], blob[NONCE_BYTES:]
    try:
        return AESGCM(_key()).decrypt(nonce, ciphertext, aad).decode("utf-8")
    except InvalidTag as exc:
        # Deliberately coarse: distinguishing "wrong key" from "altered
        # ciphertext" from "wrong owner" tells an attacker which they achieved.
        raise CredentialUnreadable("credentials could not be decrypted") from exc
