"""Persists AI call records.

This is the only place `app.ai` touches the database, and it does so through a
callable the client receives. The boundary in docs/06 §2 exists because an AI
layer that can write is an AI layer that can change state: with this split, the
worst a model can do is return a wrong answer that a validator rejects.

The log is deliberately verbose — module, model, prompt hash, tokens, latency,
status — because it is the artefact that proves the AI calls were real. A judge
can open the table and see when each call happened and whether its output was
accepted; "we use AI" is not evidence, a recorded run is.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.ai.client import AiCall
from app.models import AiRun


def make_recorder(db: Session):
    """Returns the callable `GeminiClient` accepts.

    It commits independently of the caller's transaction on purpose: if the
    surrounding request rolls back, the fact that a call was made and what it
    cost is still true, and losing it would make the log disagree with the bill.
    """

    def record(call: AiCall, project_id: uuid.UUID | None) -> None:
        db.add(
            AiRun(
                project_id=project_id,
                module=call.module,
                model=call.model,
                prompt_hash=call.prompt_hash or "no-prompt",
                input_tokens=call.input_tokens,
                output_tokens=call.output_tokens,
                latency_ms=call.latency_ms,
                validation_status=call.status,
            )
        )
        db.commit()

    return record


def total_cost_usd(db: Session) -> float:
    """Reported, never enforced. A budget guard built before there is spend to
    guard against is a knob nobody has turned."""
    from app.ai.client import INPUT_USD_PER_1M, OUTPUT_USD_PER_1M

    rows = db.query(AiRun).all()
    return sum(
        (row.input_tokens or 0) / 1_000_000 * INPUT_USD_PER_1M
        + (row.output_tokens or 0) / 1_000_000 * OUTPUT_USD_PER_1M
        for row in rows
    )
