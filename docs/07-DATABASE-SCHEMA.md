# Database Schema

14 tables. The DDL below is the specification the model layer implements; where the two differ, the model layer wins and the difference is listed in §8.

Portability rules that shape every column:

1. **Money is never a float.** `Money` is a `TypeDecorator`: `INTEGER` cents on SQLite, `NUMERIC(12,2)` on Postgres, `Decimal` at the Python boundary, `ROUND_HALF_UP` on the way in. SQLAlchemy's `Numeric` degrades to `REAL` on SQLite, which is why the custom type exists.
2. **UUID v4 primary keys.** `postgres-patterns` prefers `bigint` for index locality. Rejected here because project and version ids travel in client-shareable review links, and a sequential id is enumerable.
3. **Enums are `CHECK` constraints, not native enums.** SQLite has no enum type, and a native Postgres enum cannot be altered inside a transaction without ceremony. The set of valid values is the product's vocabulary; it belongs in the DDL where a reader will find it.

## 1. Identity

```sql
CREATE TABLE users (
    id            UUID PRIMARY KEY,
    email         VARCHAR(320) NOT NULL UNIQUE,
    name          VARCHAR(200) NOT NULL,
    password_hash VARCHAR(255),                -- NULL for magic-link-only users
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_users_email ON users (email);

CREATE TABLE invitations (
    id         UUID PRIMARY KEY,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    email      VARCHAR(320) NOT NULL,
    token_hash VARCHAR(64) NOT NULL UNIQUE,    -- SHA-256 of the token; the token itself is never stored
    expires_at TIMESTAMPTZ NOT NULL,
    used_at    TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_invitation_project_email UNIQUE (project_id, email)
);
CREATE INDEX ix_invitations_project_id ON invitations (project_id);
```

A client never gets a password. They arrive through a single-use link, exchanged server-side for a session cookie. Storing only `token_hash` means a database dump does not grant access to any project.

## 2. Projects

```sql
CREATE TABLE projects (
    id            UUID PRIMARY KEY,
    owner_id      UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    name          VARCHAR(200) NOT NULL,
    client_email  VARCHAR(320) NOT NULL,
    brief         TEXT NOT NULL,
    budget_amount INTEGER NOT NULL,            -- money type: cents in SQLite, NUMERIC(12,2) in PG
    currency      VARCHAR(3) NOT NULL DEFAULT 'USD',
    deadline      DATE,
    status        VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_project_budget_positive CHECK (budget_amount > 0),
    CONSTRAINT ck_project_currency        CHECK (currency IN ('USD')),
    CONSTRAINT ck_project_status          CHECK (status IN
        ('DRAFT','ANALYSED','AGREEMENT_DRAFT','AWAITING_CLIENT',
         'APPROVED','IN_PROGRESS','COMPLETED','CANCELLED'))
);
```

`CHECK (budget_amount > 0)` encodes a product decision: a zero-budget project is a different product, not an edge case.

```
DRAFT ──analyse──> ANALYSED ──draft──> AGREEMENT_DRAFT ──send──> AWAITING_CLIENT
                                                                    │
                                            client approves ────────┴──> APPROVED
                                                                          │
                                                            work begins ──┴──> IN_PROGRESS ──> COMPLETED
any non-terminal ─────────────────────────────────────────────────────> CANCELLED
```

## 3. Agreements

```sql
CREATE TABLE agreements (
    id              UUID PRIMARY KEY,
    project_id      UUID NOT NULL UNIQUE REFERENCES projects(id) ON DELETE CASCADE,
    current_version INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE agreement_versions (
    id                   UUID PRIMARY KEY,
    agreement_id         UUID NOT NULL REFERENCES agreements(id) ON DELETE CASCADE,
    version_no           INTEGER NOT NULL,
    status               VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
    budget_amount        INTEGER NOT NULL,
    created_by           UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    freelancer_approved_at TIMESTAMPTZ,
    client_approved_at   TIMESTAMPTZ,
    content_hash         VARCHAR(64) NOT NULL,   -- sha256 over rendered sections + milestones
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_version_number      UNIQUE (agreement_id, version_no),
    CONSTRAINT ck_version_budget_positive CHECK (budget_amount > 0),
    CONSTRAINT ck_version_no_positive     CHECK (version_no >= 1),
    CONSTRAINT ck_version_status          CHECK (status IN
        ('DRAFT','PENDING_CLIENT','APPROVED','SUPERSEDED','CANCELLED'))
);

CREATE TABLE agreement_sections (
    id          UUID PRIMARY KEY,
    version_id  UUID NOT NULL REFERENCES agreement_versions(id) ON DELETE CASCADE,
    section_key VARCHAR(40) NOT NULL,          -- 'AUTH', 'PAYMENTS', 'SCOPE', ...
    title       VARCHAR(200) NOT NULL,
    body        TEXT NOT NULL,
    ordinal     INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT uq_section_key UNIQUE (version_id, section_key)
);

CREATE TABLE milestones (
    id          UUID PRIMARY KEY,
    version_id  UUID NOT NULL REFERENCES agreement_versions(id) ON DELETE CASCADE,
    ordinal     INTEGER NOT NULL,
    title       VARCHAR(200) NOT NULL,
    deliverables JSON NOT NULL,
    acceptance   JSON NOT NULL,
    amount       INTEGER NOT NULL,
    due_date     DATE,
    status       VARCHAR(20) NOT NULL DEFAULT 'PLANNED',
    CONSTRAINT uq_milestone_ordinal      UNIQUE (version_id, ordinal),
    CONSTRAINT ck_milestone_amount_positive CHECK (amount > 0),
    CONSTRAINT ck_milestone_status          CHECK (status IN
        ('PLANNED','REQUESTED','PAID','CANCELLED'))
);
```

`agreement_sections` is the reason the whole product is defensible. A stable `section_key` per version lets the AI cite something that can be resolved to a foreign key, and lets the service verify that the quoted text genuinely appears in that section. Without stable section identity, scope comparison is an opinion.

Version immutability is a rule, not a constraint: once `status = 'APPROVED'`, no service method mutates the row or its sections. It is enforced in `services/agreements.py` and proven by `test_approved_version_is_immutable`.

```
DRAFT ──send──> PENDING_CLIENT ──both approve──> APPROVED ──new version──> SUPERSEDED
   │                    │
   └────────CANCELLED───┘
```

## 4. Indexes

Eight declared indexes beyond the implicit unique/PK ones. All are plain column lists — **none uses an expression**.

```sql
CREATE INDEX idx_projects_owner       ON projects (owner_id, created_at);
CREATE INDEX idx_versions_agreement   ON agreement_versions (agreement_id, version_no);
CREATE INDEX idx_changes_project      ON change_requests (project_id, created_at);
CREATE INDEX idx_milestones_version   ON milestones (version_id, ordinal);
CREATE INDEX idx_payments_milestone   ON payments (milestone_id);
CREATE INDEX idx_payments_status      ON payments (status) WHERE status <> 'CAPTURED';
CREATE INDEX idx_audit_project        ON audit_events (project_id, created_at);
CREATE UNIQUE INDEX uq_payments_active_per_milestone
    ON payments (milestone_id) WHERE status IN ('CREATED','APPROVED');
```

Two rules with reasons:

- **No `desc()` in an index.** Alembic silently skips indexes containing an expression, so a descending index exists in the model and never reaches the database — a drift that no test would catch unless one asserts the index exists. Both supported dialects read a B-tree backwards for `ORDER BY ... DESC`, so nothing is lost by declaring plain ascending order.
- **The partial unique index is the anti-double-pay guarantee.** `uq_payments_active_per_milestone` means a bug that tries to create a second live payment for one milestone fails at the database. Its `WHERE` clause is dialect-specific: it is declared with both `sqlite_where` and `postgresql_where`, and on any other dialect Alembic would emit a *full* unique index — one payment per milestone forever. SQLite and Postgres are the only supported targets.

`idx_payments_status` is partial because the "unpaid work" view never wants captured rows.

## 5. Change requests

```sql
CREATE TABLE change_requests (
    id              UUID PRIMARY KEY,
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    base_version_id UUID NOT NULL REFERENCES agreement_versions(id) ON DELETE CASCADE,
    requested_by    UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    raw_text        TEXT NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'ANALYSING',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_change_status CHECK (status IN
        ('ANALYSING','PROPOSED','SENT','APPROVED','REJECTED','WITHDRAWN'))
);

CREATE TABLE change_items (
    id             UUID PRIMARY KEY,
    request_id     UUID NOT NULL REFERENCES change_requests(id) ON DELETE CASCADE,
    feature        VARCHAR(300) NOT NULL,
    classification VARCHAR(30) NOT NULL,
    explanation    TEXT NOT NULL,
    confidence     VARCHAR(10) NOT NULL,
    evidence       JSON NOT NULL DEFAULT '[]',
    effort_low_h   INTEGER,
    effort_high_h  INTEGER,
    suggested_cost INTEGER,
    timeline_days  INTEGER,
    CONSTRAINT ck_change_class      CHECK (classification IN
        ('ALREADY_INCLUDED','OUT_OF_SCOPE','NEEDS_CLARIFICATION')),
    CONSTRAINT ck_change_confidence CHECK (confidence IN ('high','medium','low'))
);
```

`base_version_id` is the core guarantee: a comparison is always against **one specific version**, and that pointer never moves. `change_requests` rows in `ANALYSING` may legitimately have zero items and zero evidence — analysis is streaming — so the "evidence required" rule is *not* a database constraint.

### Why the evidence rule is not a CHECK

The obvious constraint would be `json_array_length(evidence) > 0` unless `classification = 'NEEDS_CLARIFICATION'`. Rejected:

- `json_array_length` behaves differently on SQLite and Postgres for non-array values.
- A `CHECK` can assert the array is non-empty; it cannot assert the quoted text appears in a section body — that is a cross-table containment test.

A dialect-dependent constraint that only catches half the failure is worse than an explicit, unit-tested service rule. The rule lives in `services/scope.py::validate_evidence`, and `test_unresolvable_evidence_downgrades_the_item` proves it fires.

## 6. Money

```sql
CREATE TABLE payments (
    id                UUID PRIMARY KEY,
    project_id        UUID NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    milestone_id      UUID NOT NULL REFERENCES milestones(id) ON DELETE RESTRICT,
    amount            INTEGER NOT NULL,
    currency          VARCHAR(3) NOT NULL DEFAULT 'USD',
    status            VARCHAR(30) NOT NULL DEFAULT 'CREATED',
    merchant_user_id  UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    paypal_order_id   VARCHAR(64) UNIQUE,
    paypal_capture_id VARCHAR(64) UNIQUE,
    payee_merchant_id VARCHAR(64),             -- read from the verified capture, never from the client
    idempotency_key   VARCHAR(80) NOT NULL UNIQUE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    captured_at       TIMESTAMPTZ,
    CONSTRAINT ck_payment_amount_positive CHECK (amount > 0),
    CONSTRAINT ck_payment_currency        CHECK (currency IN ('USD')),
    CONSTRAINT ck_payment_status          CHECK (status IN
        ('CREATED','APPROVED','CAPTURED','FAILED','RECONCILIATION_REQUIRED','REFUNDED'))
);

CREATE TABLE merchant_accounts (
    id                     UUID PRIMARY KEY,
    user_id                UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    encrypted_client_id     BYTEA NOT NULL,
    encrypted_client_secret BYTEA NOT NULL,
    paypal_merchant_id     VARCHAR(64),
    status                 VARCHAR(20) NOT NULL DEFAULT 'CONNECTED',
    connected_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_merchant_status CHECK (status IN ('CONNECTED','INVALID','REVOKED'))
);
```

Two `UNIQUE` constraints do the heavy lifting against double-capture: `paypal_order_id` and `paypal_capture_id`. A second row for the same capture fails at the database, not in review.

**Deletion policy is deliberately asymmetric:** anything touching money is `RESTRICT`. A project with payments cannot be deleted, because erasing a payment record is how a dispute becomes unwinnable.

```
CREATED ──payer approves──> APPROVED ──verified capture──> CAPTURED ──refund──> REFUNDED
   │                             │
   └────────declined─────────────┴──> FAILED        any mismatch with PayPal ──> RECONCILIATION_REQUIRED
```

`RECONCILIATION_REQUIRED` is not a failure state. It is the honest state for "PayPal says one thing, our record says another", and it blocks the paid UI until a human or a job resolves it.

## 7. System tables

```sql
CREATE TABLE webhook_events (
    event_id      VARCHAR(64) PRIMARY KEY,     -- PayPal's event id IS the idempotency gate
    event_type    VARCHAR(80) NOT NULL,
    payload       JSON NOT NULL,
    verified_at   TIMESTAMPTZ NOT NULL,
    processed_at  TIMESTAMPTZ,
    process_error TEXT
);

CREATE TABLE audit_events (
    id         UUID PRIMARY KEY,
    project_id UUID REFERENCES projects(id) ON DELETE SET NULL,
    actor_id   UUID REFERENCES users(id)    ON DELETE SET NULL,
    event_type VARCHAR(60) NOT NULL,
    payload    JSON NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ai_runs (
    id                UUID PRIMARY KEY,
    project_id        UUID REFERENCES projects(id) ON DELETE SET NULL,
    module            VARCHAR(40) NOT NULL,
    model             VARCHAR(60) NOT NULL,
    prompt_hash       VARCHAR(64) NOT NULL,
    input_tokens      INTEGER,
    output_tokens     INTEGER,
    latency_ms        INTEGER,
    validation_status VARCHAR(30) NOT NULL DEFAULT 'pending',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`webhook_events.event_id` as the primary key is the whole idempotency design: PayPal retries a failed delivery up to 25 times over three days, so the `INSERT` *is* the duplicate check. A conflict returns `200` and a second financial row is never written.

Audit and run logs use `ON DELETE SET NULL`, never `CASCADE`. Deleting a project must not erase the record that a payment was captured — the log is the point of the product, and `CASCADE` contradicted the "append-only" claim.

## 8. Deviations from the earlier spec, and why

| # | Earlier spec | Implemented | Reason |
|---|---|---|---|
| 1 | `invitations.token_hash` not unique | `UNIQUE` | Two live invitations must never share a hash. A unique index makes a collision fail loudly instead of granting access to the wrong project. |
| 2 | `ai_runs.project_id` with no FK | FK with `ON DELETE SET NULL` | A run log must not point at a project that no longer exists, while still surviving its deletion. The FK is the stricter choice. |
| 3 | `idx_payments_status` full index | partial `WHERE status <> 'CAPTURED'` | The view that uses it filters on that predicate. |
| 4 | `uq_payments_active_per_milestone` hand-patched into the migration | declared in the model | The model must be the single source of truth, or autogenerate drifts. |
| 5 | Evidence rule as a `CHECK` | service-layer rule | See §5. |

Deviations are recorded here rather than silently absorbed, because a schema document that does not match the schema is worse than no document.

## 9. SQLite specifics

```python
@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
```

**SQLite ignores foreign keys unless this runs on every connection.** Without it, every `ON DELETE RESTRICT` and `ON DELETE CASCADE` in this document is decorative. `test_foreign_key_enforcement_is_actually_on` attempts a violating insert and asserts it raises — if the pragma ever stops being applied, that test fails before any data is lost.

Alembic runs with `render_as_batch=True` for this dialect, since SQLite cannot `ALTER` most columns; the setting is a no-op on Postgres.

## 10. Migration discipline

Three failures hit this project and are now guarded:

| Failure | Guard |
|---|---|
| `python -m alembic` cannot find `alembic.ini` | every command passes `-c alembic.ini` explicitly |
| Autogenerate diffs against the **live DB**, so deleting a revision file chains onto a stale `down_revision` | regenerate only from an empty database, and assert `down_revision is None` on the initial revision |
| Autogenerate emits `app.money.Money()` without importing it → `NameError` | `import app.money` is in `script.py.mako`, so every generated file carries it |

Proof of the current schema: `upgrade head` → `downgrade base` → `upgrade head`, all clean, 14 `create_table` and 11 `create_index` calls in `migrations/versions/166edfd6f11e_initial_schema.py`, and 48 passing tests including `test_declared_indexes_exist_in_the_database` as a drift guard.
