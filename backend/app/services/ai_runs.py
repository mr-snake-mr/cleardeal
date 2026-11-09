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
from app.database import SessionLocal
from app.models import AiRun


def make_recorder(_db: Session | None = None):
    """Returns the callable `GeminiClient` accepts.

    It writes on **its own session**, not the caller's. Sharing the caller's
    session would mean the log commit also commits whatever else that request
    had pending — so a rolled-back request would leave half its work behind —
    and a poisoned session would turn a provider failure into a
    `PendingRollbackError`. The log has to survive the caller and cannot depend
    on it.
    """

    def record(call: AiCall, project_id: uuid.UUID | None) -> None:
        session = SessionLocal()
        try:
            session.add(
                AiRun(
                    project_id=project_id,
                    module=call.module,
                    model=call.model,
                    prompt_hash=(call.prompt_hash or "no-prompt")[:64],
                    input_tokens=call.input_tokens,
                    output_tokens=call.output_tokens,
                    latency_ms=call.latency_ms,
                    validation_status=call.status,
                )
            )
            session.commit()
        finally:
            session.close()

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
