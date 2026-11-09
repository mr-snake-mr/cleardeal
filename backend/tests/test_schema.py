"""Schema guarantees.

These are the constraints the product's promises rest on. If any stops failing
when it should, a wrong state can reach the database and no service-layer code
will save it.

Two rules for this file: every test uses freshly generated identifiers, and every
test proves a negative — the invalid thing is rejected.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal, engine
from app.models import (
    Agreement,
    AgreementVersion,
    AuditEvent,
    ChangeItem,
    ChangeRequest,
    Invitation,
    MerchantAccount,
    Milestone,
    Payment,
    Project,
    User,
    WebhookEvent,
)

EXPECTED_TABLES = {
    "agreement_sections",
    "agreement_versions",
    "agreements",
    "ai_runs",
    "audit_events",
    "change_items",
    "change_requests",
    "invitations",
    "merchant_accounts",
    "milestones",
    "payments",
    "projects",
    "users",
    "webhook_events",
}


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _user(db) -> User:
    u = User(email=f"{uuid.uuid4().hex}@example.com", name="Test User")
    db.add(u)
    db.flush()
    return u


def _project(db, owner, budget="300.00", currency="USD", status="DRAFT") -> Project:
    p = Project(
        owner_id=owner.id,
        name="Acme redesign",
        client_email=f"client-{uuid.uuid4().hex[:8]}@example.com",
        brief="Build a site.",
        budget_amount=Decimal(budget),
        currency=currency,
        status=status,
    )
    db.add(p)
    db.flush()
    return p


def _version(db, project, owner, no=1, status="DRAFT", budget=None) -> AgreementVersion:
    agreement = db.query(Agreement).filter_by(project_id=project.id).one_or_none()
    if agreement is None:
        agreement = Agreement(project_id=project.id, current_version=no)
        db.add(agreement)
        db.flush()
    v = AgreementVersion(
        agreement_id=agreement.id,
        version_no=no,
        status=status,
        budget_amount=Decimal(budget) if budget else project.budget_amount,
        created_by=owner.id,
        content_hash="0" * 64,
    )
    db.add(v)
    db.flush()
    return v


def _milestone(db, version, ordinal=1, amount="100.00", status="PLANNED") -> Milestone:
    m = Milestone(
        version_id=version.id,
        ordinal=ordinal,
        title=f"Milestone {ordinal}",
        deliverables=["thing"],
        acceptance=["it works"],
        amount=Decimal(amount),
        status=status,
    )
    db.add(m)
    db.flush()
    return m


def _payment(db, project, milestone, owner, status="CREATED", order=None, capture=None):
    p = Payment(
        project_id=project.id,
        milestone_id=milestone.id,
        amount=Decimal("100.00"),
        currency="USD",
        status=status,
        merchant_user_id=owner.id,
        paypal_order_id=order or f"ORDER-{uuid.uuid4().hex[:10]}",
        paypal_capture_id=capture,
        idempotency_key=uuid.uuid4().hex,
    )
    db.add(p)
    db.flush()
    return p


# ---------------------------------------------------------------- structure


def test_all_expected_tables_exist():
    present = set(inspect(engine).get_table_names())
    assert EXPECTED_TABLES.issubset(present), EXPECTED_TABLES - present


def test_migration_is_applied():
    with engine.connect() as conn:
        revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert revision, "alembic_version is empty — migrations did not run"


def test_foreign_key_enforcement_is_actually_on(db):
    """SQLite ignores FKs by default. If the pragma listener regresses, every
    cascade and restrict policy in this schema silently stops working.

    Reading the pragma alone is not enough: a connection-level pragma can be on
    while enforcement is off elsewhere. So this also attempts the insert that a
    disabled pragma would allow.
    """
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1

    # Negative control: a project pointing at a user that does not exist.
    orphan = Project(
        owner_id=uuid.uuid4(),
        name="orphan",
        client_email="orphan@example.test",
        brief="A brief that is long enough to be plausible for an orphan row.",
        budget_amount=Decimal("100.00"),
    )
    with pytest.raises(IntegrityError):
        db.add(orphan)
        db.flush()
    db.rollback()


def test_declared_indexes_exist_in_the_database():
    """Drift guard: an index that exists only in the model is not an index."""
    with engine.connect() as conn:
        names = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master"))}
    for expected in [
        "idx_projects_owner",
        "idx_versions_agreement",
        "idx_changes_project",
        "idx_milestones_version",
        "idx_payments_milestone",
        "idx_payments_status",
        "idx_audit_project",
        "uq_payments_active_per_milestone",
    ]:
        assert expected in names, f"{expected} is declared but missing from the database"


# ---------------------------------------------------------- money exactness


def test_money_is_stored_as_integer_cents(db):
    """Storage must be exact, not float (docs/06 §5.1)."""
    owner = _user(db)
    project = _project(db, owner, budget="1234.57")

    raw = db.execute(
        text("SELECT budget_amount FROM projects WHERE id = :id"), {"id": project.id.hex}
    ).scalar()
    assert isinstance(raw, int), f"money stored as {type(raw).__name__}, expected int cents"
    assert raw == 123457


def test_money_round_trips_without_drift(db):
    owner = _user(db)
    project = _project(db, owner, budget="0.30")
    db.flush()
    db.expire_all()

    reloaded = db.get(Project, project.id)
    assert reloaded.budget_amount == Decimal("0.30")
    assert reloaded.budget_amount + Decimal("0.20") == Decimal("0.50")


# ---------------------------------------------------------------- constraints


@pytest.mark.parametrize("budget", ["0.00", "-5.00"])
def test_budget_must_be_positive(db, budget):
    owner = _user(db)
    with pytest.raises(IntegrityError):
        _project(db, owner, budget=budget)


def test_unsupported_currency_is_rejected(db):
    owner = _user(db)
    with pytest.raises(IntegrityError):
        _project(db, owner, currency="EUR")


def test_invalid_project_status_is_rejected(db):
    owner = _user(db)
    with pytest.raises(IntegrityError):
        _project(db, owner, status="NOT_A_STATUS")


def test_invalid_version_status_is_rejected(db):
    owner = _user(db)
    project = _project(db, owner)
    with pytest.raises(IntegrityError):
        _version(db, project, owner, status="SIGNED")


def test_version_number_must_be_positive(db):
    owner = _user(db)
    project = _project(db, owner)
    with pytest.raises(IntegrityError):
        _version(db, project, owner, no=0)


def test_invalid_milestone_status_is_rejected(db):
    owner = _user(db)
    project = _project(db, owner)
    version = _version(db, project, owner)
    with pytest.raises(IntegrityError):
        _milestone(db, version, status="DONE")


def test_invalid_payment_status_is_rejected(db):
    owner = _user(db)
    project = _project(db, owner)
    version = _version(db, project, owner)
    milestone = _milestone(db, version)
    with pytest.raises(IntegrityError):
        _payment(db, project, milestone, owner, status="PAID_OUT")


def test_milestone_amount_must_be_positive(db):
    owner = _user(db)
    project = _project(db, owner)
    version = _version(db, project, owner)
    with pytest.raises(IntegrityError):
        _milestone(db, version, amount="0.00")


def test_duplicate_version_number_is_rejected(db):
    owner = _user(db)
    project = _project(db, owner)
    _version(db, project, owner, no=1)
    with pytest.raises(IntegrityError):
        _version(db, project, owner, no=1)


def test_duplicate_paypal_order_id_is_rejected(db):
    owner = _user(db)
    project = _project(db, owner)
    version = _version(db, project, owner)
    m1 = _milestone(db, version, ordinal=1)
    m2 = _milestone(db, version, ordinal=2, amount="200.00")

    _payment(db, project, m1, owner, status="FAILED", order="ORDER-DUP")
    with pytest.raises(IntegrityError):
        _payment(db, project, m2, owner, status="FAILED", order="ORDER-DUP")


def test_duplicate_paypal_capture_id_is_rejected(db):
    """The structural defence against recording one capture twice."""
    owner = _user(db)
    project = _project(db, owner)
    version = _version(db, project, owner)
    m1 = _milestone(db, version, ordinal=1)
    m2 = _milestone(db, version, ordinal=2, amount="200.00")

    _payment(db, project, m1, owner, status="CAPTURED", capture="CAPTURE-1")
    with pytest.raises(IntegrityError):
        _payment(db, project, m2, owner, status="CAPTURED", capture="CAPTURE-1")


def test_only_one_active_payment_per_milestone(db):
    """PP-15 — enforced by a partial unique index, not just service logic."""
    owner = _user(db)
    project = _project(db, owner)
    version = _version(db, project, owner)
    milestone = _milestone(db, version)

    _payment(db, project, milestone, owner, status="CREATED")
    with pytest.raises(IntegrityError):
        _payment(db, project, milestone, owner, status="APPROVED")


def test_two_captured_payments_for_one_milestone_are_allowed(db):
    """The escape hatch: history may hold more than one captured attempt."""
    owner = _user(db)
    project = _project(db, owner)
    version = _version(db, project, owner)
    milestone = _milestone(db, version)

    _payment(db, project, milestone, owner, status="CAPTURED", capture="C-1")
    _payment(db, project, milestone, owner, status="CAPTURED", capture="C-2")
    db.flush()  # must not raise


def test_invitation_is_unique_per_project_and_email(db):
    owner = _user(db)
    project = _project(db, owner)
    email = "client@example.com"
    expires = datetime(2030, 1, 1, tzinfo=UTC)

    db.add(
        Invitation(
            project_id=project.id, email=email, token_hash=uuid.uuid4().hex, expires_at=expires
        )
    )
    db.flush()

    with pytest.raises(IntegrityError):
        db.add(
            Invitation(
                project_id=project.id, email=email, token_hash=uuid.uuid4().hex, expires_at=expires
            )
        )
        db.flush()


def test_webhook_event_id_is_the_idempotency_gate(db):
    """A replayed delivery must fail at insert, so no second row is written."""
    for _ in range(2):
        db.add(
            WebhookEvent(
                event_id="WH-1",
                event_type="PAYMENT.CAPTURE.COMPLETED",
                payload={"id": "WH-1"},
                verified_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    with pytest.raises(IntegrityError):
        db.flush()


def test_invalid_classification_is_rejected(db):
    owner = _user(db)
    project = _project(db, owner)
    version = _version(db, project, owner)
    request = ChangeRequest(
        project_id=project.id,
        base_version_id=version.id,
        requested_by=owner.id,
        raw_text="add login",
    )
    db.add(request)
    db.flush()

    with pytest.raises(IntegrityError):
        db.add(
            ChangeItem(
                request_id=request.id,
                feature="Google login",
                classification="MAYBE",
                explanation="x",
                confidence="high",
            )
        )
        db.flush()


def test_invalid_merchant_status_is_rejected(db):
    owner = _user(db)
    with pytest.raises(IntegrityError):
        db.add(
            MerchantAccount(
                user_id=owner.id,
                encrypted_client_id=b"\x00a",
                encrypted_client_secret=b"\x00b",
                status="BROKEN",
            )
        )
        db.flush()


# ------------------------------------------------------- deletion semantics


def test_money_records_block_project_deletion(db):
    """Erasing a payment record is how a dispute becomes unwinnable."""
    owner = _user(db)
    project = _project(db, owner)
    version = _version(db, project, owner)
    milestone = _milestone(db, version)
    _payment(db, project, milestone, owner, status="CAPTURED", capture="C-BLOCK")

    with pytest.raises(IntegrityError):
        db.execute(text("DELETE FROM projects WHERE id = :id"), {"id": project.id.hex})
        db.flush()


def test_audit_events_survive_project_deletion(db):
    """The append-only log must not be erased with the project."""
    owner = _user(db)
    project = _project(db, owner)
    db.add(
        AuditEvent(
            project_id=project.id, actor_id=owner.id, event_type="PROJECT_CREATED", payload={}
        )
    )
    db.flush()
    event_id = db.query(AuditEvent).filter_by(event_type="PROJECT_CREATED").one().id

    # The database performs the SET NULL. If the FK were CASCADE this fails,
    # because the row would be gone.
    db.execute(text("DELETE FROM projects WHERE id = :id"), {"id": project.id.hex})
    db.flush()

    survivor = db.get(AuditEvent, event_id)
    assert survivor is not None, "audit row was erased with the project"
    assert survivor.project_id is None
    assert survivor.event_type == "PROJECT_CREATED"


# ------------------------------------------------------- invariant query


def _violations(db):
    return db.execute(
        text(
            """
            SELECT v.id FROM agreement_versions v
            JOIN milestones m ON m.version_id = v.id
            GROUP BY v.id HAVING v.budget_amount <> SUM(m.amount)
            """
        )
    ).all()


def test_budget_invariant_query_detects_a_violation(db):
    """Negative control.

    An empty-database run of this query proves nothing. Here the invariant is
    deliberately broken, so the query must return the offending version — that is
    what makes the docs/07 §2 check real rather than decorative.
    """
    owner = _user(db)
    project = _project(db, owner, budget="300.00")
    version = _version(db, project, owner, budget="300.00")
    _milestone(db, version, ordinal=1, amount="100.00")
    _milestone(db, version, ordinal=2, amount="150.00")  # 250 != 300

    found = [r[0] for r in _violations(db)]
    assert version.id.hex in found, "the invariant query failed to detect a 50.00 shortfall"


def test_budget_invariant_query_is_quiet_when_consistent(db):
    owner = _user(db)
    project = _project(db, owner, budget="300.00")
    version = _version(db, project, owner, budget="300.00")
    _milestone(db, version, ordinal=1, amount="120.00")
    _milestone(db, version, ordinal=2, amount="180.00")

    assert version.id.hex not in [r[0] for r in _violations(db)]
