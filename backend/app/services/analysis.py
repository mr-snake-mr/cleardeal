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
import re

from sqlalchemy.orm import Session

from app.ai.client import GeminiClient
from app.ai.schemas import BriefAnalysis
from app.models import AuditEvent, Project

PROMPT_PATH = (
    pathlib.Path(__file__).resolve().parent.parent / "ai" / "prompts" / "brief_analysis.md"
)


PLACEHOLDER = re.compile(r"\{\{(brief|budget|currency|deadline)\}\}")


def render_prompt(project: Project) -> str:
    """One substitution pass over the template.

    Chained `.replace()` calls re-scan text that was already inserted, so a
    brief containing the literal `{{deadline}}` would have the project's real
    deadline spliced into the client's own words. `re.sub` replaces against the
    original template only, so inserted content is never re-read.
    """
    template = PROMPT_PATH.read_text(encoding="utf-8")
    values = {
        "brief": project.brief,
        "budget": f"{project.budget_amount:.2f}",
        "currency": project.currency,
        "deadline": project.deadline.isoformat() if project.deadline else "not stated",
    }
    return PLACEHOLDER.sub(lambda match: values[match.group(1)], template)


def assert_quotes_come_from_the_brief(brief: str) -> None:
    """The central rule, enforced where it can actually be checked.

    The schema can only see that a `quote` field is non-empty. Only the service
    holds the brief, so only the service can tell a quotation from an invention.
    A fabricated quote is the failure this product exists to prevent — an
    `explicit` label the reader cannot verify is worse than no label.
    """

    def check(result: object) -> None:
        requirements = getattr(result, "requirements", [])
        haystack = " ".join(brief.split())
        for requirement in requirements:
            if getattr(requirement, "source", None) != "explicit":
                continue
            quote = " ".join((requirement.quote or "").split())
            if not quote or quote not in haystack:
                raise ValueError(
                    "an explicit requirement quoted text that is not in the brief"
                )

    return check


def analyse(db: Session, *, project: Project, client: GeminiClient) -> BriefAnalysis:
    result = client.generate(
        module="brief_analysis",
        prompt=render_prompt(project),
        schema=BriefAnalysis,
        project_id=project.id,
        validator=assert_quotes_come_from_the_brief(project.brief),
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
