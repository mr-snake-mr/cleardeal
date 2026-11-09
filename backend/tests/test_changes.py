"""M3: a citation either resolves to a stored section or it is not evidence.

This is the product's central claim, so the tests are adversarial. Every failure
mode below is a way a model can sound authoritative without having read the
agreement:

- citing a section that does not exist,
- citing a real section with a quote from somewhere else,
- citing a real section with a quote that was reworded,
- classifying with no citation at all.

All four must end as `NEEDS_CLARIFICATION`. None of them may raise: a downgrade
is a correct answer, and a 502 would hide the fact that the model guessed.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ai.schemas import Citation, ScopeItem
from app.services.changes import resolve_evidence

SECTIONS = {
    "SCOPE": (
        "The supplier will design and build a five page marketing site, "
        "including a blog index and a contact form."
    ),
    "EXCLUSIONS": "Copywriting, photography and paid advertising are not included.",
}


def _item(**overrides) -> ScopeItem:
    base = {
        "feature": "Add a sixth page",
        "classification": "OUT_OF_SCOPE",
        "explanation": "The agreement covers five pages.",
        "confidence": "high",
        "evidence": [Citation(section_key="SCOPE", quote="a five page marketing site")],
    }
    return ScopeItem(**{**base, **overrides})


def _resolve(*items):
    return resolve_evidence(items, SECTIONS)


# ------------------------------------------------------------------ happy path


def test_a_resolving_citation_is_kept_verbatim():
    [resolved] = _resolve(_item())

    assert resolved.classification == "OUT_OF_SCOPE"
    assert resolved.downgraded is False
    assert resolved.evidence[0].quote == "a five page marketing site"


def test_case_and_whitespace_differences_still_resolve():
    """A capitalisation difference is not a fabricated quote; a different word
    is. Normalising both to compare, while reporting the quote as sent, is the
    honest middle."""
    [resolved] = _resolve(
        _item(evidence=[Citation(section_key="SCOPE", quote="A  Five Page   Marketing Site")])
    )

    assert resolved.downgraded is False
    assert resolved.evidence[0].quote == "A  Five Page   Marketing Site"


# ------------------------------------------------------------------- downgrades


def test_a_citation_to_a_section_that_does_not_exist_is_downgraded():
    [resolved] = _resolve(_item(evidence=[Citation(section_key="PAYMENT", quote="net 30")]))

    assert resolved.classification == "NEEDS_CLARIFICATION"
    assert resolved.downgraded is True
    assert "PAYMENT" in resolved.reason
    assert resolved.evidence == []


def test_a_quote_stolen_from_another_section_is_downgraded():
    """The convincing failure: a real section, a real sentence, the wrong one.
    Substring search against the whole agreement would pass this."""
    [resolved] = _resolve(
        _item(evidence=[Citation(section_key="SCOPE", quote="paid advertising are not included")])
    )

    assert resolved.classification == "NEEDS_CLARIFICATION"
    assert resolved.downgraded is True


def test_a_reworded_quote_is_downgraded():
    [resolved] = _resolve(
        _item(evidence=[Citation(section_key="SCOPE", quote="a five-page marketing website")])
    )

    assert resolved.classification == "NEEDS_CLARIFICATION"


def test_a_classification_with_no_evidence_is_downgraded():
    """`ALREADY_INCLUDED` with nothing behind it is a guess wearing a label."""
    [resolved] = _resolve(_item(classification="ALREADY_INCLUDED", evidence=[]))

    assert resolved.classification == "NEEDS_CLARIFICATION"
    assert "evidence" in resolved.reason.lower()


def test_a_too_short_quote_is_downgraded():
    """Without a floor, "a" matches almost any section and proves nothing."""
    [resolved] = _resolve(_item(evidence=[Citation(section_key="SCOPE", quote="a")]))

    assert resolved.classification == "NEEDS_CLARIFICATION"


def test_one_bad_citation_downgrades_the_item_even_when_another_resolves():
    """Evidence is judged as a set. A true citation beside a false one cannot
    make the false one true."""
    [resolved] = _resolve(
        _item(
            evidence=[
                Citation(section_key="SCOPE", quote="a five page marketing site"),
                Citation(section_key="SCOPE", quote="a sixth page is included"),
            ]
        )
    )

    assert resolved.classification == "NEEDS_CLARIFICATION"


# --------------------------------------------------------- the honest downgrade


def test_needs_clarification_needs_no_evidence():
    [resolved] = _resolve(
        _item(
            classification="NEEDS_CLARIFICATION",
            confidence="low",
            evidence=[],
        )
    )

    assert resolved.classification == "NEEDS_CLARIFICATION"
    assert resolved.downgraded is False, "it was already honest; nothing to downgrade"


def test_a_downgrade_forces_low_confidence():
    """Keeping `high` on a downgraded item would relabel the same guess."""
    [resolved] = _resolve(_item(confidence="high", evidence=[]))

    assert resolved.confidence == "low"


# ---------------------------------------------------------------- the schema


def test_confidence_cannot_be_a_number():
    """`high|medium|low` is a judgement a model can make. `0.91` is a number it
    cannot, and a fabricated one is worse than none."""
    with pytest.raises(ValidationError):
        ScopeItem(
            feature="X",
            classification="OUT_OF_SCOPE",
            explanation="Because.",
            confidence=0.91,
            evidence=[],
        )


def test_a_citation_must_carry_text():
    with pytest.raises(ValidationError):
        Citation(section_key="SCOPE", quote="")
