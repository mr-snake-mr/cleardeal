"""Change-request shapes (docs/08 §6).

`section_id` is in the response and was produced by the server: the model
returned a `section_key` and a quote, and the service resolved the key. A model
that never saw a database id cannot invent a plausible one.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.projects import MoneyStr


class ChangeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_text: str = Field(min_length=10, max_length=4_000)


class EvidenceOut(BaseModel):
    section_key: str
    section_id: uuid.UUID
    quote: str


class ChangeItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    feature: str
    classification: str
    explanation: str
    confidence: str
    evidence: list[EvidenceOut]
    suggested_cost: MoneyStr | None
    timeline_days: int | None
    # Why the classification moved to NEEDS_CLARIFICATION, when it did.
    downgrade_reason: str | None


class ChangeRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    base_version_id: uuid.UUID
    raw_text: str
    status: str
    created_at: datetime


class ChangeCheckOut(BaseModel):
    change_request: ChangeRequestOut
    items: list[ChangeItemOut]


class ChangeApprovalOut(BaseModel):
    id: uuid.UUID
    status: str
    # Set only when this signature was the second one.
    resulting_version_id: uuid.UUID | None = None
