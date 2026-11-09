"""M3: evidence resolution.

The rule the product is named for: a classification is evidence or it is a guess,
and a guess must say so.

`resolve_evidence` takes what the model said and the sections of the version it
was shown, and asks one question per citation — does this section exist, and does
that text appear in *this* section. Everything that fails becomes
`NEEDS_CLARIFICATION`.

Three decisions worth stating:

- **Substring against the cited section, not against the whole document.** A
  sentence that exists in `EXCLUSIONS` does not prove a claim about `SCOPE`, and
  a document-wide search would pass exactly the most convincing wrong answer.
- **Whitespace collapsed, case folded, for comparison only.** A capitalisation
  difference is not a fabricated quote; a different word is. The quote is
  reported exactly as the model sent it.
- **A failure downgrades, it never raises.** A 502 would hide the guess; a
  downgrade puts it on screen with its reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.ai.schemas import Citation, ScopeItem

# Below this, a quote matches almost any section and proves nothing.
MIN_QUOTE_CHARS = 8

NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"


@dataclass(slots=True)
class ResolvedItem:
    feature: str
    classification: str
    explanation: str
    confidence: str
    evidence: list[Citation] = field(default_factory=list)
    downgraded: bool = False
    reason: str | None = None


def _normalise(text: str) -> str:
    return " ".join(text.split()).casefold()


def _resolve_one(item: ScopeItem, sections: dict[str, str]) -> ResolvedItem:
    resolved = ResolvedItem(
        feature=item.feature,
        classification=item.classification,
        explanation=item.explanation,
        confidence=item.confidence,
        evidence=list(item.evidence),
    )

    # Already honest. Nothing to verify, nothing to downgrade.
    if item.classification == NEEDS_CLARIFICATION:
        return resolved

    if not item.evidence:
        return _downgrade(resolved, "no evidence was offered for this classification")

    for citation in item.evidence:
        body = sections.get(citation.section_key)
        if body is None:
            return _downgrade(resolved, f"section {citation.section_key} does not exist")

        quote = _normalise(citation.quote)
        if len(quote) < MIN_QUOTE_CHARS:
            return _downgrade(resolved, "the quote is too short to prove anything")
        if quote not in _normalise(body):
            # The most convincing failure there is: a real section, a real
            # sentence, the wrong one.
            return _downgrade(
                resolved, f"the quote does not appear in {citation.section_key}"
            )

    return resolved


def _downgrade(resolved: ResolvedItem, reason: str) -> ResolvedItem:
    resolved.classification = NEEDS_CLARIFICATION
    resolved.downgraded = True
    resolved.reason = reason
    # Keeping `high` would relabel the same guess.
    resolved.confidence = "low"
    # A citation that could not be checked must not travel as if it had been.
    resolved.evidence = []
    return resolved


def resolve_evidence(items: list[ScopeItem], sections: dict[str, str]) -> list[ResolvedItem]:
    """`sections` maps `section_key` to that section's body for one version."""
    return [_resolve_one(item, sections) for item in items]
