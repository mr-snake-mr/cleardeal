"""Project request and response shapes.

Two boundary rules from docs/08 §3, both enforced here rather than in the
service:

- `budget_amount` is a decimal string, never a JSON float. A float cannot
  represent every cent exactly, and a payment system that rounds is a payment
  system that eventually pays the wrong amount.
- `extra="forbid"`. A client that posts `amount` or `status` gets a `422`; a
  silent ignore would look like it worked.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, model_validator

from app.schemas.auth import Email

# Money crosses the wire as a fixed-scale string: "300.00", never 300.0.
MoneyStr = Annotated[
    Decimal,
    PlainSerializer(lambda value: f"{value:.2f}", return_type=str, when_used="json"),
]


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    client_email: Email
    # 20 characters is the floor because a one-word brief cannot be analysed;
    # 100_000 is a boundary check, not a product limit.
    brief: str = Field(min_length=20, max_length=100_000)
    budget_amount: Decimal = Field(gt=0, decimal_places=2, max_digits=12)
    deadline: date | None = None

    @model_validator(mode="after")
    def _no_nulls_on_required(self):
        # `{"name": null}` used to reach the database and raise IntegrityError,
        # which escaped as a 500. A required field cannot be unset by PATCH.
        for field in ("name", "client_email", "brief"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self

    @model_validator(mode="after")
    def _at_least_one_field(self):
        # An empty PATCH is a client bug, not a no-op: answering 200 hides it.
        if not self.model_fields_set:
            raise ValueError("send at least one field to change")
        return self


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    client_email: str
    brief: str
    budget_amount: MoneyStr
    currency: str
    deadline: date | None
    status: str
    created_at: datetime


class ProjectList(BaseModel):
    items: list[ProjectOut]
    total: int


class ProjectUpdate(BaseModel):
    """Partial by construction: absent fields are untouched.

    `budget_amount` is deliberately absent. Every send and approve compares the
    milestones to it, so a budget that can move afterwards invalidates a check
    that has already passed. A client sees the change as 422, not as a silent
    no-op, because `extra="forbid"` makes the field name the error.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    client_email: Email | None = None
    brief: str | None = Field(default=None, min_length=20, max_length=10_000)
    deadline: date | None = None

    @model_validator(mode="after")
    def _no_nulls_on_required(self):
        # `{"name": null}` used to reach the database and raise IntegrityError,
        # which escaped as a 500. A required field cannot be unset by PATCH.
        for field in ("name", "client_email", "brief"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self

    @model_validator(mode="after")
    def _at_least_one_field(self):
        # An empty PATCH is a client bug, not a no-op: answering 200 hides it.
        if not self.model_fields_set:
            raise ValueError("send at least one field to change")
        return self
