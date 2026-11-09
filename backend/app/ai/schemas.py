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
