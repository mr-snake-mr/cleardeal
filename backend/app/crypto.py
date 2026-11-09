"""AES-GCM for merchant credentials at rest.

Why GCM and not CBC: GCM authenticates. A CBC blob that someone flips a bit in
decrypts to garbage and looks like a wrong key; a GCM blob raises. For a value
that becomes an API credential, "tampered" must be distinguishable from "wrong".

Key handling, and the honest limit: the key is 32 bytes derived from
`CREDENTIALS_ENCRYPTION_KEY` by SHA-256. That is deterministic and accepts any
passphrase, but it is *not* a password KDF — there is no salt and no work factor.
The key is meant to be 32 random bytes in hex, where SHA-256 is a no-op on the
entropy; the passphrase path exists so a demo secret still produces a usable key
rather than a crash. `shortcut: sha256 instead of a KDF, upgrade to argon2id or
a KMS-held key before real credentials are stored.`

A missing key raises. A credential store that silently writes plaintext when the
key is absent is worse than one that refuses to run.
"""

from __future__ import annotations

import hashlib
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings

NONCE_BYTES = 12


class EncryptionKeyMissing(RuntimeError):
    """No key configured. Not a 500: the operator has not finished setting up."""


class CredentialUnreadable(RuntimeError):
    """Wrong key, or the ciphertext was altered. Both mean "do not use this"."""


def _key() -> bytes:
    raw = settings.credentials_encryption_key
    if not raw:
        raise EncryptionKeyMissing(
            "CREDENTIALS_ENCRYPTION_KEY is not set; merchant credentials cannot "
            "be stored or read."
        )
    return hashlib.sha256(raw.encode("utf-8")).digest()


def encrypt(plaintext: str) -> bytes:
    """`nonce || ciphertext||tag`. A fresh nonce per call, never reused."""
    nonce = os.urandom(NONCE_BYTES)
    return nonce + AESGCM(_key()).encrypt(nonce, plaintext.encode("utf-8"), None)


def decrypt(blob: bytes) -> str:
    if len(blob) <= NONCE_BYTES:
        raise CredentialUnreadable("ciphertext is too short to contain a nonce")
    nonce, ciphertext = blob[:NONCE_BYTES], blob[NONCE_BYTES:]
    try:
        return AESGCM(_key()).decrypt(nonce, ciphertext, None).decode("utf-8")
    except InvalidTag as exc:
        # Deliberately coarse: distinguishing "wrong key" from "altered
        # ciphertext" tells an attacker which of the two they achieved.
        raise CredentialUnreadable("credentials could not be decrypted") from exc
