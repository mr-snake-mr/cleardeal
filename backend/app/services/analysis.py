"""M1: read a brief, separate what was said from what was guessed.

The prompt lives in `app/ai/prompts/brief_analysis.md`, not in this file, so a
change to the wording is a diff a reviewer can read. `ai_runs.prompt_hash` then
ties every stored result to the exact prompt that produced it.

Nothing here writes a requirement the model marked `inferred` into anything that
looks contractual. The analysis is an artefact of the run: it is returned to the
caller and recorded in the audit trail, and the project only moves from `DRAFT`
to `ANALYSED`. Agreement rows are built later, from what a human accepts.
"""

from __future__ import annotations

import pathlib

from sqlalchemy.orm import Session

from app.ai.client import GeminiClient
from app.ai.schemas import BriefAnalysis
from app.models import AuditEvent, Project

PROMPT_PATH = (
    pathlib.Path(__file__).resolve().parent.parent / "ai" / "prompts" / "brief_analysis.md"
)


def render_prompt(project: Project) -> str:
    """Plain placeholder substitution: no template engine for three fields."""
    template = PROMPT_PATH.read_text(encoding="utf-8")
    return (
        template.replace("{{brief}}", project.brief)
        .replace("{{budget}}", f"{project.budget_amount:.2f}")
        .replace("{{currency}}", project.currency)
        .replace("{{deadline}}", project.deadline.isoformat() if project.deadline else "not stated")
    )


def analyse(db: Session, *, project: Project, client: GeminiClient) -> BriefAnalysis:
    result = client.generate(
        module="brief_analysis",
        prompt=render_prompt(project),
        schema=BriefAnalysis,
        project_id=project.id,
    )

    project.status = "ANALYSED"
    db.add(
        AuditEvent(
            project_id=project.id,
            actor_id=project.owner_id,
            event_type="ANALYSIS_COMPLETED",
            payload={
                "requirements": len(result.requirements),
                "explicit": sum(1 for r in result.requirements if r.source == "explicit"),
                "inferred": sum(1 for r in result.requirements if r.source == "inferred"),
                "ambiguities": len(result.ambiguities),
                "risks": len(result.risks),
            },
        )
    )
    db.commit()
    return result
