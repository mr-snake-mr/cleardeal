"""Password hashing and session tokens, stdlib only.

No new dependency: `passlib`/`argon2` would be the production choice, and
docs/01 §8 makes a new dependency an ask-first item. PBKDF2-HMAC-SHA256 at
600 000 iterations is the OWASP-sanctioned stdlib answer, so the honest
trade-off is "fewer algorithms, no supply chain" rather than "no hashing".

Two decisions worth stating:

1. The hash format is self-describing (`pbkdf2_sha256$iters$salt$hash`), so the
   iteration count can be raised later without invalidating stored passwords.
2. Session tokens are signed, not encrypted. They carry a user id and an
   expiry — both public — and the signature is what makes them unforgeable.
   Nothing secret goes in the payload, so encryption would add complexity
   without adding a guarantee.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid

from app.config import settings

_ALGORITHM = "pbkdf2_sha256"
_ITERATIONS = 600_000
_SALT_BYTES = 16
_TOKEN_VERSION = "v1"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def hash_password(password: str) -> str:
    """Return a self-describing hash. Same password never hashes twice the same."""
    if not password:
        raise ValueError("password must not be empty")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return f"{_ALGORITHM}${_ITERATIONS}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str | None) -> bool:
    """Constant-time check. A None hash (magic-link-only user) never verifies."""
    if not stored or not password:
        return False
    try:
        algorithm, iterations, salt_b64, digest_b64 = stored.split("$")
        if algorithm != _ALGORITHM:
            return False
        expected = _unb64(digest_b64)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), _unb64(salt_b64), int(iterations)
        )
    except (ValueError, TypeError):
        # A corrupt hash is a failed login, not a 500. The alternative is an
        # error path that tells an attacker which accounts have broken rows.
        return False
    return hmac.compare_digest(actual, expected)


def _sign(payload: str) -> str:
    mac = hmac.new(
        settings.session_secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256
    )
    return _b64(mac.digest())


def issue_session(user_id: uuid.UUID, *, ttl_hours: int | None = None) -> str:
    """`v1.<payload>.<signature>` — the payload is base64url JSON."""
    expires = int(time.time()) + 3600 * (ttl_hours or settings.session_ttl_hours)
    payload = _b64(json.dumps({"sub": str(user_id), "exp": expires}).encode("utf-8"))
    return f"{_TOKEN_VERSION}.{payload}.{_sign(payload)}"


def read_session(token: str | None) -> uuid.UUID | None:
    """Return the user id, or None for any malformed, tampered or expired token."""
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != _TOKEN_VERSION:
        return None
    _, payload, signature = parts
    # Signature first: parsing an unverified payload is how token bugs become
    # injection bugs.
    if not hmac.compare_digest(signature, _sign(payload)):
        return None
    try:
        data = json.loads(_unb64(payload))
        if int(data["exp"]) < int(time.time()):
            return None
        return uuid.UUID(data["sub"])
    except (ValueError, KeyError, TypeError):
        return None
