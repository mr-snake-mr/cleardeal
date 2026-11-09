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

import pathlib
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.schemas import ChangeAnalysis, Citation, ScopeItem
from app.models import (
    Agreement,
    AgreementSection,
    AgreementVersion,
    AuditEvent,
    ChangeItem,
    ChangeRequest,
    Invitation,
    Milestone,
    Project,
)
from app.services.agreements import (
    AgreementError,
    RoleNotYours,
    content_hash_for,
)

# Below this, a quote matches almost any section and proves nothing.
MIN_QUOTE_CHARS = 12

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


def _contains_whole_words(haystack: str, needle: str) -> bool:
    """Substring search that refuses to match the middle of a word.

    `"pagination control"` is a substring of `"pagination controls"`, so a bare
    `in` accepts a quotation the agreement does not make. The ends of a quote
    must land on word boundaries.

    What this cannot do, and does not claim to: it cannot tell that quoting
    `"included"` out of `"are not included"` reverses the meaning. Negation is
    not a boundary problem; the reviewer reads the quote in the UI.
    """
    start = 0
    while True:
        index = haystack.find(needle, start)
        if index == -1:
            return False
        before = haystack[index - 1] if index else " "
        end = index + len(needle)
        after = haystack[end] if end < len(haystack) else " "
        if not before.isalnum() and not after.isalnum():
            return True
        start = index + 1


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
        if not _contains_whole_words(_normalise(body), quote):
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


# ------------------------------------------------------------------ the call

PROMPT_PATH = (
    pathlib.Path(__file__).resolve().parent.parent / "ai" / "prompts" / "change_analysis.md"
)
PLACEHOLDER = re.compile(r"\{\{(sections|change)\}\}")


def render_prompt(raw_text: str, sections: dict[str, str]) -> str:
    """One substitution pass: chained replaces re-scan inserted text."""
    block = "\n\n".join(f"[{key}]\n{body}" for key, body in sections.items())
    values = {"sections": block, "change": raw_text}
    return PLACEHOLDER.sub(
        lambda match: values[match.group(1)],
        PROMPT_PATH.read_text(encoding="utf-8"),
    )


def analyse_change(
    *,
    client,
    raw_text: str,
    sections: dict[str, str],
    project_id: uuid.UUID | None = None,
) -> list[ResolvedItem]:
    analysis = client.generate(
        module="change_analysis",
        prompt=render_prompt(raw_text, sections),
        schema=ChangeAnalysis,
        project_id=project_id,
    )
    # Resolution is a post-step, not a validator: a failed citation is a
    # downgrade the user should see, not a 502 that hides the guess.
    return resolve_evidence(analysis.items, sections)


# ------------------------------------------------------------- persistence

def current_version(db: Session, project_id: uuid.UUID) -> AgreementVersion | None:
    agreement = db.scalars(
        select(Agreement).where(Agreement.project_id == project_id)
    ).one_or_none()
    if agreement is None or not agreement.current_version:
        return None
    return db.scalars(
        select(AgreementVersion).where(
            AgreementVersion.agreement_id == agreement.id,
            AgreementVersion.version_no == agreement.current_version,
        )
    ).one_or_none()


def sections_for(db: Session, version_id: uuid.UUID) -> tuple[dict[str, str], dict[str, uuid.UUID]]:
    rows = db.scalars(
        select(AgreementSection).where(AgreementSection.version_id == version_id)
    ).all()
    return {row.section_key: row.body for row in rows}, {
        row.section_key: row.id for row in rows
    }


def create_request(
    db: Session,
    *,
    project: Project,
    version: AgreementVersion,
    requester_id: uuid.UUID,
    raw_text: str,
    resolved: list[ResolvedItem],
    section_ids: dict[str, uuid.UUID],
    now: datetime,
) -> tuple[ChangeRequest, list[ChangeItem]]:
    request = ChangeRequest(
        project_id=project.id,
        base_version_id=version.id,
        requested_by=requester_id,
        raw_text=raw_text,
        status="PROPOSED",
    )
    db.add(request)
    db.flush()

    items: list[ChangeItem] = []
    for resolved_item in resolved:
        evidence = [
            {
                "section_key": citation.section_key,
                # The server's id, never the model's: it only ever returned a key.
                "section_id": str(section_ids[citation.section_key]),
                "quote": citation.quote,
            }
            for citation in resolved_item.evidence
        ]
        item = ChangeItem(
            request_id=request.id,
            feature=resolved_item.feature,
            classification=resolved_item.classification,
            explanation=resolved_item.explanation,
            confidence=resolved_item.confidence,
            evidence=evidence,
            downgrade_reason=resolved_item.reason,
            # An ALREADY_INCLUDED item must never carry a price.
            suggested_cost=None,
        )
        db.add(item)
        items.append(item)

    db.add(
        AuditEvent(
            project_id=project.id,
            actor_id=requester_id,
            event_type="CHANGE_REQUEST_ANALYSED",
            payload={
                "change_request_id": str(request.id),
                "base_version_no": version.version_no,
                "items": len(items),
                "downgraded": sum(1 for item in resolved if item.downgraded),
            },
        )
    )
    db.commit()
    db.refresh(request)
    for item in items:
        db.refresh(item)
    return request, items


def get_for_owner(
    db: Session, *, owner_id: uuid.UUID, request_id: uuid.UUID
) -> tuple[ChangeRequest, list[ChangeItem]] | None:
    request = db.scalars(
        select(ChangeRequest)
        .join(Project, ChangeRequest.project_id == Project.id)
        .where(ChangeRequest.id == request_id, Project.owner_id == owner_id)
    ).one_or_none()
    if request is None:
        return None
    items = db.scalars(
        select(ChangeItem).where(ChangeItem.request_id == request.id).order_by(ChangeItem.feature)
    ).all()
    return request, list(items)


# ------------------------------------------------------- approving a change

class ChangeNotOpen(AgreementError):
    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(f"this change request is {status}")


def _may_sign(db: Session, *, request: ChangeRequest, role: str, actor) -> None:
    """Same rule as the agreement: the owner signs as freelancer, and the client
    role opens only for the account named on this project's invitation."""
    project = db.get(Project, request.project_id)
    if project is None:
        raise RoleNotYours(role)
    if role == "freelancer":
        if actor.id != project.owner_id:
            raise RoleNotYours(role)
        return
    if actor.id == project.owner_id:
        raise RoleNotYours(role)
    named = db.scalars(
        select(Invitation).where(
            Invitation.project_id == project.id,
            func.lower(Invitation.email) == actor.email.lower(),
        )
    ).one_or_none()
    if named is None:
        raise RoleNotYours(role)


def get_request_for_participant(db: Session, *, request_id: uuid.UUID, actor):
    request = db.get(ChangeRequest, request_id)
    if request is None:
        return None
    project = db.get(Project, request.project_id)
    if project is None:
        return None
    if project.owner_id == actor.id:
        return request
    named = db.scalars(
        select(Invitation).where(
            Invitation.project_id == project.id,
            func.lower(Invitation.email) == actor.email.lower(),
        )
    ).one_or_none()
    return request if named is not None else None


def approve_request(
    db: Session, *, request: ChangeRequest, role: str, actor, now: datetime
) -> tuple[ChangeRequest, AgreementVersion | None]:
    """One signature at a time; the second one drafts the next version."""
    if request.status != "PROPOSED":
        raise ChangeNotOpen(request.status)
    _may_sign(db, request=request, role=role, actor=actor)

    if role == "freelancer":
        request.freelancer_approved_at = now
    else:
        request.client_approved_at = now

    resulting: AgreementVersion | None = None
    if request.freelancer_approved_at and request.client_approved_at:
        request.status = "APPROVED"
        resulting = _draft_next_version(db, request=request, actor_id=actor.id)

    db.add(
        AuditEvent(
            project_id=request.project_id,
            actor_id=actor.id,
            event_type="CHANGE_REQUEST_APPROVED",
            payload={
                "change_request_id": str(request.id),
                "role": role,
                "resulting_version_id": str(resulting.id) if resulting else None,
            },
        )
    )
    db.commit()
    db.refresh(request)
    return request, resulting


def _draft_next_version(
    db: Session, *, request: ChangeRequest, actor_id: uuid.UUID
) -> AgreementVersion:
    """Copy the signed version, record what was accepted, price nothing.

    The new version carries the old budget and the old milestones on purpose. A
    rule that invented a price here would be the model making a commercial
    decision, and the sum invariant would then be checking the model against
    itself. Instead the draft is unshippable until a human edits it.
    """
    base = db.get(AgreementVersion, request.base_version_id)
    if base is None:
        raise ChangeNotOpen("no base version")

    agreement = db.get(Agreement, base.agreement_id)
    if agreement is None:
        raise ChangeNotOpen("no agreement")

    draft = AgreementVersion(
        agreement_id=agreement.id,
        version_no=agreement.current_version + 1,
        status="DRAFT",
        budget_amount=base.budget_amount,
        created_by=actor_id,
        content_hash="0" * 64,
    )
    db.add(draft)
    db.flush()

    for section in db.scalars(
        select(AgreementSection).where(AgreementSection.version_id == base.id)
    ).all():
        db.add(
            AgreementSection(
                version_id=draft.id,
                section_key=section.section_key,
                title=section.title,
                body=section.body,
                ordinal=section.ordinal,
            )
        )

    items = db.scalars(
        select(ChangeItem)
        .where(
            ChangeItem.request_id == request.id,
            ChangeItem.classification != "NEEDS_CLARIFICATION",
        )
        .order_by(ChangeItem.feature)
    ).all()
    lines = [f"- {item.feature} ({item.classification}): {item.explanation}" for item in items]
    quotes = [
        f'  cited: "{evidence["quote"]}" in {evidence["section_key"]}'
        for item in items
        for evidence in item.evidence
    ]
    body = (
        "The following requests were accepted against version "
        f"{base.version_no} and are recorded here. Amounts for this work are set "
        "by the parties in the milestones; this section does not price them.\n\n"
        + "\n".join(lines + quotes)
    )
    db.add(
        AgreementSection(
            version_id=draft.id,
            section_key="CHANGES",
            title="Agreed changes",
            body=body,
            ordinal=99,
        )
    )

    for milestone in db.scalars(
        select(Milestone).where(Milestone.version_id == base.id).order_by(Milestone.ordinal)
    ).all():
        db.add(
            Milestone(
                version_id=draft.id,
                ordinal=milestone.ordinal,
                title=milestone.title,
                deliverables=list(milestone.deliverables),
                acceptance=list(milestone.acceptance),
                amount=milestone.amount,
                due_date=milestone.due_date,
            )
        )

    db.flush()
    db.refresh(draft)
    agreement.current_version = draft.version_no
    draft.content_hash = content_hash_for(draft)
    return draft
