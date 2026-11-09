"""M2: turn an analysed brief into a draft whose milestones sum to the budget.

The prompt asks the model to do the arithmetic. This module does not trust it.
`assert_milestones_sum_to_budget` runs inside the client's semantic gate, before
the `ok` row is written, so a draft that misses the total is a `502` with a
`sematic_error` row — not an agreement that quietly pays out 29,900 of 30,000.

Money crosses this boundary as integer cents (docs/05 §4) and as `Decimal` at the
edges. A float anywhere in the middle is how a total becomes 29999.999999999996.
"""

from __future__ import annotations

import pathlib
import re
import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

from app.ai.schemas import AgreementDraft, BriefAnalysis

PROMPT_PATH = (
    pathlib.Path(__file__).resolve().parent.parent / "ai" / "prompts" / "agreement_draft.md"
)

PLACEHOLDER = re.compile(r"\{\{(budget|currency|requirements)\}\}")


class DraftClient(Protocol):
    """The slice of `GeminiClient` this module needs."""

    def generate(self, **kwargs) -> AgreementDraft: ...


def to_cents(amount: Decimal) -> int:
    """Exact conversion, or an error.

    Half-up rounding here is not a formatting choice: rounding a budget silently
    moves money the client never agreed to. A budget that is not representable in
    cents is a data problem for whoever entered it.
    """
    quantised = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if quantised != amount:
        raise ValueError(f"{amount} is not an amount of money in cents")
    return int(quantised * 100)


def render_draft_prompt(analysis: BriefAnalysis, budget: Decimal, currency: str) -> str:
    """One substitution pass, for the reason `analysis.render_prompt` documents:
    chained replaces re-scan text that was already inserted."""
    template = PROMPT_PATH.read_text(encoding="utf-8")
    requirements = "\n".join(
        f"- [{requirement.source}] {requirement.text}" for requirement in analysis.requirements
    )
    values = {
        "budget": f"{budget:.2f}",
        "currency": currency,
        "requirements": requirements,
    }
    return PLACEHOLDER.sub(lambda match: values[match.group(1)], template)


def assert_milestones_sum_to_budget(draft: AgreementDraft, budget_cents: int) -> None:
    """The invariant every payment below rests on.

    The message names both numbers on purpose: a caller told only "invalid" has
    to guess whether the model or the budget is wrong.
    """
    total = sum(milestone.amount_cents for milestone in draft.milestones)
    if total != budget_cents:
        raise ValueError(
            f"milestone amounts sum to {total} cents, but the budget is {budget_cents} cents"
        )


def draft_agreement(
    *,
    analysis: BriefAnalysis,
    budget: Decimal,
    currency: str,
    client: DraftClient,
    project_id: uuid.UUID | None = None,
) -> AgreementDraft:
    budget_cents = to_cents(budget)
    return client.generate(
        module="agreement_draft",
        prompt=render_draft_prompt(analysis, budget, currency),
        schema=AgreementDraft,
        project_id=project_id,
        validator=lambda result: assert_milestones_sum_to_budget(result, budget_cents),
    )
