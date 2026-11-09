"""Agreement request and response shapes (docs/08 §3, §4).

Money is a fixed-scale string here for the same reason it is on projects: a JSON
float cannot represent every cent, and "300.00" survives a round trip that 300.0
does not. `extra="forbid"` on the inputs so a client that posts `status` or
`content_hash` gets a 422 instead of a silent ignore.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.projects import MoneyStr


class AgreementCreate(BaseModel):
    """The requirements a human accepted from the analysis.

    The analysis is not persisted, so the accepted texts are what the draft is
    built from — the server does not re-run the model to recover them.
    """

    model_config = ConfigDict(extra="forbid")

    accepted_requirements: list[str] = Field(min_length=1, max_length=40)


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["freelancer", "client"]


class RejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=5, max_length=2_000)


class SectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    section_key: str
    title: str
    body: str
    ordinal: int


class MilestoneOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ordinal: int
    title: str
    deliverables: list[str]
    acceptance: list[str]
    amount: MoneyStr
    due_date: date | None
    status: str


class VersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version_no: int
    status: str
    budget_amount: MoneyStr
    content_hash: str
    # Recomputed on every read. A stored hash that nobody re-checks is a number,
    # not evidence; this is what turns it into something a reader can rely on.
    content_intact: bool
    created_at: datetime
    freelancer_approved_at: datetime | None
    client_approved_at: datetime | None
    sections: list[SectionOut]
    milestones: list[MilestoneOut]


class MilestoneIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ordinal: int = Field(ge=1)
    title: str = Field(min_length=3, max_length=200)
    deliverables: list[str] = Field(min_length=1, max_length=20)
    acceptance: list[str] = Field(min_length=1, max_length=20)
    amount: MoneyStr


class AgreementPatch(BaseModel):
    """The pricing step, and the only place a human sets a number for a change.

    `budget_amount` and `milestones` travel together on purpose: raising one
    without the other is exactly the mismatch the sum check exists to catch, so
    the API lets a client send either and the service judges the combined result.
    """

    model_config = ConfigDict(extra="forbid")

    budget_amount: MoneyStr | None = None
    milestones: list[MilestoneIn] | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def _at_least_one(self):
        if not self.model_fields_set:
            raise ValueError("send budget_amount or milestones")
        return self
