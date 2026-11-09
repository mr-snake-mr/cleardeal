"""Output schemas for the AI modules.

These are the contract between the model and the rest of the system, and they
are deliberately stricter than the JSON Schema handed to the provider. docs/05
§3.2 records why: Gemini enforces `minItems` and silently strips `minLength`,
`maxLength`, `maxProperties` and `uniqueItems`. Anything enforced only in the
generation schema is not enforced, so every rule is re-stated here and checked
again on the way in.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Source(StrEnum):
    explicit = "explicit"
    inferred = "inferred"


class Requirement(BaseModel):
    text: str = Field(min_length=3)
    source: Source
    quote: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def explicit_must_be_quoted(self) -> Requirement:
        """The rule the whole product rests on, at the smallest possible scale.

        An `explicit` claim that cannot produce a verbatim quote is an inferred
        claim wearing a costume. Refusing it here means a fabricated quote is a
        `502` the caller can see, not a plausible line in a document.
        """
        if self.source is Source.explicit:
            if not self.quote or not self.quote.strip():
                raise ValueError("an explicit requirement must quote the brief")
            if len(self.quote.strip()) < 5:
                raise ValueError("an explicit requirement's quote is too short to verify")
        return self


class Ambiguity(BaseModel):
    text: str = Field(min_length=3)
    why_it_matters: str = Field(min_length=3)


class Risk(BaseModel):
    text: str = Field(min_length=3)
    severity: Literal["low", "medium", "high"]


class Assumption(BaseModel):
    text: str = Field(min_length=3)


class BriefAnalysis(BaseModel):
    """What M1 returns. Empty lists are valid; invented content is not."""

    requirements: list[Requirement] = Field(min_length=1, max_length=40)
    ambiguities: list[Ambiguity] = Field(default_factory=list, max_length=20)
    risks: list[Risk] = Field(default_factory=list, max_length=20)
    assumptions: list[Assumption] = Field(default_factory=list, max_length=20)


class SectionDraft(BaseModel):
    """One addressable part of the agreement.

    `section_key` is the identifier M3 cites against, so it is constrained here:
    a citation that cannot be resolved to a stored row is not evidence.
    """

    section_key: str = Field(min_length=2, max_length=40, pattern=r"^[A-Z][A-Z0-9_]*$")
    title: str = Field(min_length=3, max_length=200)
    body: str = Field(min_length=10)


class MilestoneDraft(BaseModel):
    """Cents, as an integer.

    docs/05 §4: "Milestone amounts are integers in USD cents." A float amount is
    the one shape where the sum invariant can fail for a reason nobody can see,
    so the type makes it unrepresentable rather than validating it later.
    """

    ordinal: int = Field(ge=1)
    title: str = Field(min_length=3, max_length=200)
    deliverables: list[str] = Field(min_length=1, max_length=20)
    acceptance: list[str] = Field(min_length=1, max_length=20)
    # `strict=True` is not pedantry. In lax mode the string "10.00" validates as
    # 10 — a model that answered in dollars would be stored as ten cents, a 100x
    # error that no sum check downstream could detect.
    amount_cents: int = Field(gt=0, strict=True)


class AgreementDraft(BaseModel):
    """What M2 returns. The sum rule is not checked here — only the service
    knows the budget it must be checked against."""

    title: str = Field(min_length=3, max_length=200)
    sections: list[SectionDraft] = Field(min_length=1, max_length=20)
    milestones: list[MilestoneDraft] = Field(min_length=1, max_length=20)


class Citation(BaseModel):
    """A pointer the service must be able to resolve (docs/05 §3.1).

    `section_key` is a key, not an id: the model never sees database ids, so it
    cannot invent a plausible-looking UUID.
    """

    section_key: str = Field(min_length=2, max_length=40)
    quote: str = Field(min_length=1, max_length=600)


class ScopeItem(BaseModel):
    feature: str = Field(min_length=3, max_length=300)
    classification: Literal["ALREADY_INCLUDED", "OUT_OF_SCOPE", "NEEDS_CLARIFICATION"]
    explanation: str = Field(min_length=5, max_length=1200)
    # `high | medium | low`. No numeric confidence: `0.91` is a number a model
    # cannot know, and a fabricated one is worse than none (docs/05 §3.1).
    confidence: Literal["high", "medium", "low"]
    evidence: list[Citation] = Field(default_factory=list, max_length=6)


class ChangeAnalysis(BaseModel):
    """What M3 returns, before the service has checked any of it."""

    items: list[ScopeItem] = Field(min_length=1, max_length=12)
