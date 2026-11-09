"""M2: a draft whose milestones do not sum to the budget is not a draft.

The invariant is the one every later payment rests on: a milestone total that
drifts from the budget is an agreement nobody can be held to. It is enforced in
the service layer, against the project's stored budget, because the model cannot
be trusted to check its own arithmetic — and because a rule that lives only in
the prompt is a rule that does not exist (docs/05 §3.2).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.ai.schemas import AgreementDraft, MilestoneDraft
from app.services.agreements import (
    assert_milestones_sum_to_budget,
    draft_agreement,
    render_draft_prompt,
    to_cents,
)


def _milestone(ordinal: int = 1, cents: int = 10_000) -> MilestoneDraft:
    return MilestoneDraft(
        ordinal=ordinal,
        title=f"Milestone {ordinal}",
        deliverables=["a thing"],
        acceptance=["it works"],
        amount_cents=cents,
    )


def _draft(*cents: int) -> AgreementDraft:
    return AgreementDraft(
        title="Website build",
        sections=[
            {
                "section_key": "SCOPE",
                "title": "Scope",
                "body": "The work described in the analysed brief and nothing more.",
            }
        ],
        milestones=[_milestone(index + 1, amount) for index, amount in enumerate(cents)],
    )


REQUIREMENTS = ["A five page site"]


class StubClient:
    """Records what the service asked for, then behaves like the real client."""

    def __init__(self, result: object) -> None:
        self.result = result
        self.kwargs: dict | None = None

    def generate(self, **kwargs):
        self.kwargs = kwargs
        validator = kwargs.get("validator")
        if validator is not None:
            validator(self.result)
        return self.result


# ----------------------------------------------------------------- the arithmetic


def test_cents_conversion_is_exact():
    """A float here is how 300.10 becomes 30009.999999999996."""
    assert to_cents(Decimal("300.00")) == 30_000
    assert to_cents(Decimal("333.33")) == 33_333
    assert to_cents(Decimal("0.10")) == 10


def test_a_budget_with_sub_cent_precision_is_refused():
    """Silently rounding the budget would move money that was never agreed."""
    with pytest.raises(ValueError):
        to_cents(Decimal("1.005"))


def test_a_sum_equal_to_the_budget_passes():
    assert_milestones_sum_to_budget(_draft(10_000, 20_000), 30_000)


def test_a_sum_that_misses_the_budget_names_both_numbers():
    """The message has to be actionable: 'invalid' tells nobody what to fix."""
    with pytest.raises(ValueError) as caught:
        assert_milestones_sum_to_budget(_draft(10_000, 19_900), 30_000)

    message = str(caught.value)
    assert "29900" in message and "30000" in message


def test_a_single_milestone_carrying_the_whole_budget_passes():
    assert_milestones_sum_to_budget(_draft(30_000), 30_000)


# -------------------------------------------------------------------- the schema


def test_a_milestone_amount_must_be_a_whole_positive_number_of_cents():
    for bad in (0, -1, Decimal("10.5"), "10.00"):
        with pytest.raises(ValidationError):
            MilestoneDraft(
                ordinal=1,
                title="Bad",
                deliverables=["x"],
                acceptance=["y"],
                amount_cents=bad,
            )


def test_a_draft_must_carry_at_least_one_milestone():
    """Everything else here is valid, so the only thing that can fail is the
    empty milestone list — otherwise this test passes for the wrong reason."""
    with pytest.raises(ValidationError):
        AgreementDraft(
            title="Empty",
            sections=[
                {
                    "section_key": "SCOPE",
                    "title": "Scope",
                    "body": "The work described in the analysed brief.",
                }
            ],
            milestones=[],
        )


# ------------------------------------------------------------------- the wiring


def test_draft_agreement_sends_the_budget_and_wires_the_invariant():
    """The helper is not enough; the call has to use it.

    Without this, the invariant could pass its unit tests and never run on a
    real draft — the exact shape of the I1 bug the last audit found.
    """
    stub = StubClient(_draft(12_000, 18_000))

    result = draft_agreement(
        requirements=REQUIREMENTS, budget=Decimal("300.00"), currency="USD", client=stub
    )

    assert result.milestones[0].amount_cents == 12_000
    assert stub.kwargs is not None
    assert stub.kwargs["module"] == "agreement_draft"
    assert stub.kwargs["validator"] is not None
    # And the validator it wired is the real one, not a lambda that always passes.
    with pytest.raises(ValueError):
        stub.kwargs["validator"](_draft(12_000, 17_000))


def test_the_prompt_carries_the_budget_currency_and_requirements():
    prompt = render_draft_prompt(REQUIREMENTS, Decimal("300.00"), "USD")

    assert "300.00" in prompt
    assert "USD" in prompt
    assert "A five page site" in prompt
