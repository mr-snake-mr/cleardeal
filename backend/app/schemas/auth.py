"""Request and response shapes for auth.

`extra="forbid"` on every request: docs/08 §1 makes an undeclared field a `422`
rather than a silent ignore. That is what stops a client posting `id` or
`password_hash` and hoping.

Email validation is a deliberate stdlib regex rather than `EmailStr`.
`EmailStr` needs the `email-validator` package, and docs/01 §8 makes a new
dependency an ask-first item. The regex is stricter than nothing and looser
than RFC 5322 — it rejects the shapes that matter (no `@`, no domain, spaces)
and it normalises case, which doc 11 S-1 requires anyway. A real address is
proven by delivery, not by a regex.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


def _normalise_email(value: str) -> str:
    value = value.strip().lower()
    if len(value) > 320 or not _EMAIL_RE.match(value):
        raise ValueError("not an email address")
    return value


Email = Annotated[str, AfterValidator(_normalise_email)]


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Email
    name: str = Field(min_length=1, max_length=200)
    # 12 is a floor, not a policy: length beats composition rules, and a
    # composition rule that rejects a 40-character passphrase is a worse trade.
    password: str = Field(min_length=12, max_length=200)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Email
    password: str = Field(min_length=1, max_length=200)


class UserOut(BaseModel):
    """Never includes `password_hash`. There is no field for it to leak through."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    name: str
    created_at: datetime
