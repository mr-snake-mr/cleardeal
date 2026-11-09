"""M2 and the agreement version lifecycle.

Two halves, deliberately in one module because they share the one rule that
matters: **the version a client approved is the version that gets paid.**

- Drafting: the prompt asks the model to split the budget across milestones. The
  service does not trust that arithmetic. `assert_milestones_sum_to_budget` runs
  inside the client's semantic gate, before the `ok` row is written.
- Lifecycle: `DRAFT → PENDING_CLIENT → APPROVED`, with `SUPERSEDED` and
  `CANCELLED` off to the side. `send` re-checks the sum, because creation is not
  the last time a row can change. `approve` refuses a version that is no longer
  current, and refuses a version whose rendered content no longer matches the
  hash it was given.

Money crosses these boundaries as integer cents in the AI layer (docs/05 §4) and
as `Decimal` in the database. A float anywhere in the middle is how a total
becomes 29999.999999999996.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai.schemas import AgreementDraft
from app.models import Agreement, AgreementSection, AgreementVersion, AuditEvent, Milestone, Project

PROMPT_PATH = (
    pathlib.Path(__file__).resolve().parent.parent / "ai" / "prompts" / "agreement_draft.md"
)

PLACEHOLDER = re.compile(r"\{\{(budget|currency|requirements)\}\}")


class AgreementError(Exception):
    """Base for the refusals the router turns into specific 409s."""


class MilestoneSumMismatch(AgreementError):
    def __init__(self, budget: Decimal, total: Decimal) -> None:
        self.budget = budget
        self.total = total
        super().__init__(f"milestones total {total} but the budget is {budget}")


class StaleVersion(AgreementError):
    def __init__(self, approved: int, current: int) -> None:
        self.approved = approved
        self.current = current
        super().__init__(f"version {approved} is not current (current is {current})")


class InvalidState(AgreementError):
    def __init__(self, status: str, wanted: str) -> None:
        self.status = status
        super().__init__(f"version is {status}; {wanted} required")


class ContentChanged(AgreementError):
    def __init__(self) -> None:
        super().__init__("the rendered content no longer matches the stored hash")


class VersionRace(AgreementError):
    """Two callers drafted at the same time and both took version n+1.

    The unique constraint is the real guard; this turns the violation into a
    retryable 409 instead of a 500 an operator has to read a traceback to
    understand. `services/auth.py` does the same for a duplicate email.
    """

    def __init__(self) -> None:
        super().__init__("another draft was created at the same time; reload and retry")


def _flush_or_race(db: Session) -> None:
    """Flush, translating a uniqueness violation into a retryable refusal.

    The violation surfaces at `flush()`, which is where SQLAlchemy actually
    executes the INSERT — catching it at `commit()` would miss it entirely.
    """
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise VersionRace() from exc


class DraftClient(Protocol):
    """The slice of `GeminiClient` this module needs."""

    def generate(self, **kwargs) -> AgreementDraft: ...


# ------------------------------------------------------------------ drafting


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


def render_draft_prompt(requirements: list[str], budget: Decimal, currency: str) -> str:
    """One substitution pass, for the reason `analysis.render_prompt` documents:
    chained replaces re-scan text that was already inserted.

    The requirements arrive as plain strings because that is what the endpoint
    has: the caller sends the ones a human accepted (docs/08 §3), and the
    analysis itself is not persisted.
    """
    template = PROMPT_PATH.read_text(encoding="utf-8")
    values = {
        "budget": f"{budget:.2f}",
        "currency": currency,
        "requirements": "\n".join(f"- {text}" for text in requirements),
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
    requirements: list[str],
    budget: Decimal,
    currency: str,
    client: DraftClient,
    project_id: uuid.UUID | None = None,
) -> AgreementDraft:
    budget_cents = to_cents(budget)
    return client.generate(
        module="agreement_draft",
        prompt=render_draft_prompt(requirements, budget, currency),
        schema=AgreementDraft,
        project_id=project_id,
        validator=lambda result: assert_milestones_sum_to_budget(result, budget_cents),
    )


# ---------------------------------------------------------------- persistence


def content_hash_for(version: AgreementVersion) -> str:
    """sha256 over what a reader would see: the rendered sections and the money.

    Deliberately excludes ids, timestamps and status. Those change for reasons
    that have nothing to do with what was agreed, and a hash that moves when a
    row is touched proves nothing.
    """
    payload = {
        "version_no": version.version_no,
        "budget": f"{version.budget_amount:.2f}",
        "sections": [
            {"key": s.section_key, "title": s.title, "body": s.body}
            for s in sorted(version.sections, key=lambda s: s.ordinal)
        ],
        "milestones": [
            {
                "ordinal": m.ordinal,
                "title": m.title,
                "deliverables": m.deliverables,
                "acceptance": m.acceptance,
                "amount": f"{m.amount:.2f}",
                # A due date is part of what was agreed. Leaving it out of the
                # hash would let it move without the hash noticing.
                "due_date": m.due_date.isoformat() if m.due_date else None,
            }
            for m in sorted(version.milestones, key=lambda m: m.ordinal)
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_version(
    db: Session, *, project: Project, draft: AgreementDraft, author_id: uuid.UUID
) -> AgreementVersion:
    agreement = db.scalars(
        select(Agreement).where(Agreement.project_id == project.id)
    ).one_or_none()
    if agreement is None:
        agreement = Agreement(project_id=project.id, current_version=0)
        db.add(agreement)
        _flush_or_race(db)

    if agreement.current_version:
        previous = db.scalars(
            select(AgreementVersion).where(
                AgreementVersion.agreement_id == agreement.id,
                AgreementVersion.version_no == agreement.current_version,
            )
        ).one()
        # A draft that was replaced before anyone approved it is not a version of
        # the agreement; leaving it DRAFT would let it be sent later.
        if previous.status in ("DRAFT", "PENDING_CLIENT"):
            previous.status = "SUPERSEDED"

    version = AgreementVersion(
        agreement_id=agreement.id,
        version_no=agreement.current_version + 1,
        status="DRAFT",
        budget_amount=project.budget_amount,
        created_by=author_id,
        # Placeholder: the real hash needs the children, which do not exist yet.
        content_hash="0" * 64,
    )
    db.add(version)
    _flush_or_race(db)

    for ordinal, section in enumerate(draft.sections):
        db.add(
            AgreementSection(
                version_id=version.id,
                section_key=section.section_key,
                title=section.title,
                body=section.body,
                ordinal=ordinal,
            )
        )
    for milestone in draft.milestones:
        db.add(
            Milestone(
                version_id=version.id,
                ordinal=milestone.ordinal,
                title=milestone.title,
                deliverables=list(milestone.deliverables),
                acceptance=list(milestone.acceptance),
                amount=(Decimal(milestone.amount_cents) / 100).quantize(Decimal("0.01")),
            )
        )
    db.flush()
    db.refresh(version)

    agreement.current_version = version.version_no
    version.content_hash = content_hash_for(version)
    db.add(
        AuditEvent(
            project_id=project.id,
            actor_id=author_id,
            event_type="AGREEMENT_DRAFTED",
            payload={
                "version_no": version.version_no,
                "milestones": len(version.milestones),
                "sections": len(version.sections),
                "total": f"{sum(m.amount for m in version.milestones):.2f}",
            },
        )
    )
    db.commit()
    db.refresh(version)
    return version


def get_for_owner(
    db: Session, *, owner_id: uuid.UUID, version_id: uuid.UUID
) -> AgreementVersion | None:
    """Scoped in SQL, like the project queries: a non-owner gets `None`, which
    the router turns into 404 rather than 403."""
    return db.scalars(
        select(AgreementVersion)
        .join(Agreement, AgreementVersion.agreement_id == Agreement.id)
        .join(Project, Agreement.project_id == Project.id)
        .where(AgreementVersion.id == version_id, Project.owner_id == owner_id)
    ).one_or_none()


# ------------------------------------------------------------------ lifecycle


def send(db: Session, *, version: AgreementVersion, actor_id: uuid.UUID) -> AgreementVersion:
    if version.status != "DRAFT":
        raise InvalidState(version.status, "DRAFT")
    total = sum((m.amount for m in version.milestones), start=Decimal("0.00"))
    if total != version.budget_amount:
        raise MilestoneSumMismatch(version.budget_amount, total)
    version.status = "PENDING_CLIENT"
    db.add(
        AuditEvent(
            project_id=_project_id(db, version),
            actor_id=actor_id,
            event_type="AGREEMENT_SENT",
            payload={"version_no": version.version_no},
        )
    )
    db.commit()
    db.refresh(version)
    return version


def approve(
    db: Session,
    *,
    version: AgreementVersion,
    role: str,
    actor_id: uuid.UUID,
    now,
) -> AgreementVersion:
    agreement = db.get(Agreement, version.agreement_id)
    if agreement is None or version.version_no != agreement.current_version:
        raise StaleVersion(version.version_no, agreement.current_version if agreement else 0)
    if version.status != "PENDING_CLIENT":
        raise InvalidState(version.status, "PENDING_CLIENT")
    # The document a reader sees must still be the document that was hashed.
    if content_hash_for(version) != version.content_hash:
        raise ContentChanged()
    # And it must still add up. `send` checked once; a row can change between
    # the two calls, and APPROVED is the state payments read.
    total = sum((m.amount for m in version.milestones), start=Decimal("0.00"))
    if total != version.budget_amount:
        raise MilestoneSumMismatch(version.budget_amount, total)

    if role == "freelancer":
        version.freelancer_approved_at = now
    else:
        version.client_approved_at = now
    if version.freelancer_approved_at and version.client_approved_at:
        version.status = "APPROVED"

    db.add(
        AuditEvent(
            project_id=_project_id(db, version),
            actor_id=actor_id,
            event_type="AGREEMENT_APPROVED",
            payload={
                "version_no": version.version_no,
                "role": role,
                # Honesty about the evidence: the role is asserted by the
                # caller, not proven by a second session. Without this the log
                # reads as "the client approved", which is a claim the log
                # cannot support (docs/16 A-11).
                "role_source": "self_asserted",
            },
        )
    )
    db.commit()
    db.refresh(version)
    return version


def reject(
    db: Session, *, version: AgreementVersion, reason: str, actor_id: uuid.UUID
) -> AgreementVersion:
    if version.status != "PENDING_CLIENT":
        raise InvalidState(version.status, "PENDING_CLIENT")
    version.status = "CANCELLED"
    db.add(
        AuditEvent(
            project_id=_project_id(db, version),
            actor_id=actor_id,
            event_type="AGREEMENT_REJECTED",
            payload={"version_no": version.version_no, "reason": reason},
        )
    )
    db.commit()
    db.refresh(version)
    return version


def _project_id(db: Session, version: AgreementVersion) -> uuid.UUID:
    agreement = db.get(Agreement, version.agreement_id)
    assert agreement is not None  # noqa: S101 - FK makes this impossible
    return agreement.project_id
