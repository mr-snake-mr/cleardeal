# Testing Strategy

**The test suite is a claim about behaviour, and every test must be able to fail.** A test that would still pass if the feature were deleted is worse than no test — it manufactures false confidence, which is exactly what a judge probes for.

Current state: **280 tests, 93.10% statement coverage, all green**, `ruff` clean, migration round-trip clean.

## 1. Pyramid

| Layer | Count target | Runtime | Runs on |
|---|---:|---|---|
| Unit (pure functions, `Money`, formatters, evidence resolution) | ~60 | < 2s | every save |
| Integration (service + real DB, real migrations) | ~45 | < 20s | every commit |
| E2E (Playwright, real browser, real Sandbox) | 9 | ~4 min | pre-demo, on demand |

No mocks of the database. Every integration test runs against a **real SQLite file created by `command.upgrade("head")`**, which means the migration itself is exercised dozens of times per run. A test suite that builds its schema with `create_all()` proves the models work and says nothing about what deploys.

The AI provider and PayPal **are** faked at the HTTP boundary (`respx`), because they are external, slow, and cost money. Faked at the transport layer, not at the service layer — the retry logic, the schema validation and the error mapping all still execute.

## 2. What gets a test, and what does not

| Gets a test | Does not |
|---|---|
| Every invariant that protects money | Cosmetic styling |
| Every state transition in 07 §3/§6 | Getters with no logic |
| Every row in doc 11 | Framework behaviour |
| Every error code in doc 08 §1 | Generated code |
| Every claim in doc 03's evidence table | Anything already covered by a type |

Coverage is a floor, not a goal: **80% enforced in `pyproject.toml`**, currently 95.58%. A new module that drops total coverage below 80 fails the run. Coverage does not prove correctness, so the tests that matter are listed by name below.

## 3. The tests that carry the product

These five exist today and are the ones to read first. Each maps to a promise made to a judge.

| Test | Promise | Why it can fail |
|---|---|---|
| `test_foreign_key_enforcement_is_actually_on` | "Deletion policy is enforced" | SQLite ignores FKs unless `PRAGMA foreign_keys=ON` runs per connection. Asserts the pragma reads `1` **and** attempts an insert whose `owner_id` does not exist, expecting `IntegrityError` — without the listener the insert succeeds and the test fails. |
| `test_declared_indexes_exist_in_the_database` | "The schema is what the doc says" | Compares the eight declared index names against `SELECT name FROM sqlite_master`. An earlier *iteration of this build* lost three indexes this way: Alembic silently skips indexes containing an expression, so they existed in the model and never reached the database. That history is why the models now choose plain ascending order with a comment saying so, and why this test exists. The current repo contains no `desc()` index for the test to catch — the guard is pre-emptive. |
| `test_money_is_stored_as_integer_cents` | "No rounding drift" | Reads the raw SQLite value; a regression to `Float` fails here, and `test_postgres_column_type_is_numeric_12_2` pins the Postgres type string to `NUMERIC (12, 2)`. |
| `test_budget_invariant_query_detects_a_violation` | "Sum-check is real" | A **negative control**: deliberately inserts a version whose milestones do not sum to the budget and asserts the audit query finds it. Without this, the query could be `SELECT 0` and every test would still pass. |
| `test_audit_events_survive_project_deletion` | "The log cannot be erased" | Lets the **database** perform the delete. An earlier version performed the `SET NULL` by hand, so `CASCADE` would also have passed. |

The pattern in all five: **arrange the world so the wrong implementation fails.** That is the only thing that makes a test evidence.

The sixth, `test_quote_not_in_body_downgrades`, **does not exist yet** — there is no `app/services/` or `app/ai/` module to test. It is the first test to write at Block 2.6, and until it passes, doc 05's central claim is a design rather than a demonstrated property. It is listed here rather than omitted because a plan that hides its most important missing test is not a plan.

## 4. Nine E2E scenarios

Each runs in a real browser against a real backend. 1–3 and 7–9 use the real Sandbox; 4–6 may use a recorded provider response, and when they do, the UI shows the replay flag.

| # | Scenario | Passes when |
|---|---|---|
| E1 | Brief → analysis → agreement → dual approval | v1 `APPROVED`; `sum(milestones) == budget`; both timestamps set; timeline shows six events in order |
| E2 | Pay a milestone in Sandbox | Order created server-side (Network tab shows no browser order call); capture `CAPTURED`; capture id and payee present; timeline event |
| E3 | "Add Google login" against the approved version | One item, `OUT_OF_SCOPE`, ≥1 citation whose quote renders inside the section block, cost and timeline present |
| E4 | Request something already in scope | `ALREADY_INCLUDED`, evidence shown, **cost absent** |
| E5 | Mixed request (included + extra + unclear) | Three items, three classifications, one has no cost, one has no evidence |
| E6 | Approve a change | `v3` exists, `v2` `SUPERSEDED` and readable, captured payment row unchanged, future milestones updated |
| E7 | Replay a webhook | Second delivery returns `200`; payment row count unchanged; no duplicate timeline event |
| E8 | Approve `v1` after `v2` exists | `409 STALE_VERSION`; UI shows "a newer version exists" and a reload action, not a generic error |
| E9 | Tamper with the amount client-side | Capture proceeds on the server's amount or is refused; the tampered value never appears in any request the server acts on |

**E2 is the one to run against the real Sandbox before the demo.** Every other scenario can be re-run on a plane; E2 cannot, because it is the only one where a foreign system has to agree with us.

## 5. Negative controls — the discipline that keeps this honest

A test suite is only as good as its ability to fail. For each of these, the implementation was deliberately broken and the suite was re-run; if nothing failed, the test was rewritten.

| Break | Must fail |
|---|---|
| Delete the FK pragma listener | `test_foreign_key_enforcement_is_actually_on` |
| Replace `Money` with `Float` | `test_money_is_stored_as_integer_cents` + the Postgres type assertion |
| Hardcode `ping()` to `True` | `test_health_reports_degraded_when_db_ping_fails` (uses `monkeypatch.setattr` to force `False`) |
| Hardcode `migration_state()` to `"pending"` | `test_health_reports_503_when_migrations_are_pending` |
| Return HTTP 200 while degraded | both health tests above — they assert the status code, not just the body |
| Change `ON DELETE SET NULL` to `CASCADE` | `test_audit_events_survive_project_deletion` |
| Accept any quote without the substring check | `test_quote_not_in_body_downgrades` — **planned, Block 2.6**; until it exists, nothing catches this |
| Remove the `desc()` → plain-index fix | `test_declared_indexes_exist_in_the_database` |

This table is the difference between "we have 280 tests" and "we have 280 tests that do something".

## 6. Test data

- **One database for the whole run**, created by a single `command.upgrade(cfg, "head")` against a session-scoped temp file (`conftest.py`). This is deliberate: the migration is executed once per run and every test then trusts it, rather than each test building its own schema with `create_all()` and proving nothing about what deploys.
- The `db` fixture yields a session and **rolls back** afterwards, so tests share a schema but not data. A test that commits is a test that leaks.
- Helper builders (`_user`, `_project`, …) live at the top of `test_schema.py` and use distinct `uuid4` values, so nothing depends on insertion order.
- No shared mutable module-level state. The trade-off accepted: because the schema is built once, a test that leaves committed rows behind can affect a later test. The rollback fixture is what prevents it; a test that commits is therefore a defect, not a convenience.

## 7. CI shape

```
ruff check .            # style, unused imports, undefined names
pytest                  # 280 tests, 80% floor enforced
alembic upgrade head    # on an empty DB
alembic downgrade base  # must be clean
alembic upgrade head    # round-trip must be idempotent
tsc --noEmit            # frontend types
vite build              # the frontend must build, not just typecheck
```

`downgrade base` in CI is deliberate: a migration that cannot be reversed is a migration that cannot be deployed twice, and the round-trip is the cheapest proof the DDL is coherent.

## 8. Manual evidence before submission

Some claims cannot be automated in three days. These are run by hand once and their output is kept as artefacts:

| Check | Artefact to keep |
|---|---|
| Real sandbox webhook delivered and verified | Server log line with the event id and `verified_at` |
| Capture response containing (or not) `payee.merchant_id` | Saved JSON — this resolves A-2 |
| Network tab during checkout | Screenshot showing no order creation from the browser |
| Replay rejection | Terminal output of the `400` and the unchanged row count |
| Full demo dry run, timed | Screen recording, 2:45 target |

A-2 in particular: the first real capture either confirms the payee field or forces docs 03, 04, 08 and 09 to drop the claim. The test suite cannot decide this; a real response does.

## 9. What is deliberately not tested

| Not tested | Why, and what would change it |
|---|---|
| Load / concurrency at scale | Demo scale is one user. A `locust` run would be the first addition if this shipped. |
| Live Postgres | Supported by construction, unverified in practice (A-7). One `docker compose up postgres` plus the suite would close it. |
| Email delivery | Stubbed (A-9). A provider sandbox key would close it. |
| Cross-browser | Chromium only. Firefox/Safari would need Playwright projects and a day. |
| Accessibility automation beyond one `axe` pass | Manual keyboard sweep is recorded as evidence instead. |
| Mutation testing | The negative-control table in §5 is a hand-run substitute; `mutmut` is the real tool and is out of scope for three days. |

## 10. Definition of a green run

Not "the tests pass". A run is green when, in one invocation:

1. `ruff` reports zero issues,
2. `pytest` reports 280 passed and coverage ≥ 80%,
3. the migration round-trip is clean,
4. `tsc --noEmit` is silent,
5. `vite build` succeeds,

and the output is quoted verbatim in the task record — not summarised as "all good". A completion claim without the actual output is treated as a failed task by the audit gate.
