# System Architecture

One process, one database, one browser app. Anything more elaborate is a liability at this scale — every extra moving part is a thing that can be down during a three-minute demo.

## 1. Shape

```
┌───────────────────────────────────────────────────────────────────┐
│ Browser (React 18 + Vite + TypeScript, Tailwind 3.4)              │
│   /                        freelancer workspace                   │
│   /projects/:id            analysis, agreement, milestones        │
│   /projects/:id/changes    scope check + change proposals         │
│   /review/:token           client review (magic link, no login)   │
└──────────────────────────────┬────────────────────────────────────┘
                               │ JSON over HTTPS, cookie session
┌──────────────────────────────▼────────────────────────────────────┐
│ FastAPI (uvicorn)                                                 │
│                                                                   │
│  routers/        thin: parse, authorise, call a service, serialise │
│  services/       ALL business rules live here                      │
│  ai/             client, prompt files, validators                  │
│  payments/       PayPal client, credential resolution, capture FSM │
│  models/         SQLAlchemy 2.1 declarative, 14 tables             │
│  money.py        Money TypeDecorator (cents in SQLite, NUMERIC(12,2) in PG) │
│  database.py     engine, session factory, FK pragma, migration probe│
└───────┬───────────────────────┬───────────────────────┬───────────┘
        │                       │                       │
        ▼                       ▼                       ▼
  ┌───────────┐          ┌─────────────┐         ┌──────────────┐
  │ SQLite    │          │ Gemini API  │         │ PayPal REST  │
  │ (dev) or  │          │ structured  │         │ sandbox      │
  │ Postgres  │          │ output      │         │ Orders v2    │
  └───────────┘          └─────────────┘         └──────┬───────┘
                                                        │ webhook
                                                 ┌──────▼───────┐
                                                 │ POST         │
                                                 │ /webhooks/   │
                                                 │ paypal       │
                                                 └──────────────┘
```

Three external dependencies, all fallible, all wrapped. Nothing else is allowed to be on the network path.

## 2. Module boundaries

| Layer | May import | May NOT import | Why |
|---|---|---|---|
| `routers/` | `services/`, `schemas/` | `models/` directly, `payments/`, `ai/` | A route that touches an ORM object can accidentally commit. Services own transactions. |
| `services/` | `models/`, `money`, `ai/`, `payments/` | `routers/`, `fastapi.Request` | A service must be callable from a test, a job, or a webhook without an HTTP request existing. |
| `ai/` | `schemas/` | `models/`, `services/`, database session | The AI layer must be pure: text in, validated object out. It cannot write. |
| `payments/` | `money`, HTTP client | `services/` | Avoids a cycle; the payment client reports facts, the service decides state. |

This is enforced by review plus one architectural test (`tests/test_boundaries.py`, Task 6) that asserts no module under `routers/` imports `app.models` — a violation fails CI, not code review.

## 3. Request path, always the same

```
request
  -> pydantic schema (shape, types, ranges)
  -> auth dependency (session or magic-link token)
  -> authorisation check (does this user own / co-own this project)
  -> service call (one transaction)
      -> business rule
      -> ORM write
      -> audit_event append
  -> response schema
```

Every mutation writes exactly one `audit_events` row **in the same transaction**. Not after, not via a background task — if the write rolls back, the log never happened, which is the only way the log cannot lie.

## 4. Sequence — milestone payment (F-5)

```
Freelancer      Frontend         API                    PayPal        DB
   │  request    │                │                       │           │
   │────────────>│ POST /payments │                       │           │
   │             │───────────────>│ amount = milestones.amount         │
   │             │                │───────────────────────────────────>│ INSERT CREATED
   │             │                │ POST /v2/checkout/orders           │
   │             │                │──────────────────────>│            │
   │             │                │<──────────────────────│ order id   │
   │             │                │───────────────────────────────────>│ order id
   │             │<───────────────│ {approve_url, order_id}            │
   │  click      │                │                       │           │
   │────────────>│ PayPal JS SDK  │                       │           │
   │             │───────────────────────────────────────>│ approve   │
   │             │ POST /payments/{id}/capture            │           │
   │             │───────────────>│                       │           │
   │             │                │ assert status, amount match        │
   │             │                │ POST /v2/checkout/orders/{id}/capture
   │             │                │──────────────────────>│           │
   │             │                │<──────────────────────│ COMPLETED │
   │             │                │ assert status/amount/currency/payee│
   │             │                │───────────────────────────────────>│ CAPTURED
   │             │<───────────────│ {status: CAPTURED, captured_at}    │
```

The rule the diagram encodes: **the order creation and the capture both happen on the server**. The browser never sees a `client_secret`, never builds an amount, and cannot influence one. A judge inspecting the Network tab sees no order-creation call from the browser.

## 5. Sequence — scope check (F-6)

```
Client        API                     AI                DB
  │  "add       │                      │                 │
  │  google     │                      │                 │
  │  login"     │                      │                 │
  │────────────>│ POST /changes        │                 │
  │             │────────────────────────────────────────>│ INSERT ANALYSING
  │             │ load approved version + sections       │
  │             │<────────────────────────────────────────│
  │             │ score candidate sections (deterministic)
  │             │ M3 prompt: request + top-k sections    │
  │             │─────────────────────>│                 │
  │             │<─────────────────────│ classification  │
  │             │                     │ + citations     │
  │             │ resolve key -> id, assert quote substring
  │             │ any unresolvable -> NEEDS_CLARIFICATION
  │             │ M4 effort/cost for OUT_OF_SCOPE items  │
  │             │─────────────────────>│                 │
  │             │<─────────────────────│ ranges          │
  │             │────────────────────────────────────────>│ INSERT items
  │             │                      │                  │ status=PROPOSED
  │<────────────│ per-feature result + evidence           │
```

The step that matters is between the two AI calls: **resolution and containment**. That is the product. Everything above is a wrapper around it.

## 6. Deployment

| Environment | Database | App | Webhooks |
|---|---|---|---|
| Local | SQLite file, `PRAGMA foreign_keys=ON` per connection | `uvicorn --reload` | Not tested locally — PayPal refuses non-HTTPS |
| Demo | SQLite file (single writer, no concurrency risk at demo scale) | Render free web service | HTTPS on 443, real delivery |
| Production-shaped | Postgres | Same image | Same endpoint |

Postgres is supported by construction — `Money` switches to `NUMERIC(12,2)`, batch mode in Alembic becomes a no-op, partial indexes use `postgresql_where`. It is not *tested* against a live Postgres in this build; that is recorded as an unverified assumption (16, A-7), not asserted as working.

### Deployment shape

```
render.yaml          one web service + one Postgres (optional, unused in demo)
Dockerfile           python:3.12-slim, non-root, no build tools in final layer
frontend/dist        built and served as static files by the same service
```

Serving the SPA from the API's static mount removes CORS from the demo path entirely. `allowed_origins` stays for local dev only.

## 7. Cross-cutting concerns

| Concern | Decision |
|---|---|
| Logging | Structured JSON, one line per request: method, path, status, duration, request id. Never bodies, never tokens. |
| Request id | `X-Request-Id` generated if absent, echoed, included in every error body and log line |
| Errors | One shape: `{code, message, request_id, details?}`. `code` is a stable string, not a number the UI must map. |
| Config | Pydantic `Settings`, `.env` in dev, env vars in deploy. `paypal_env` is `Literal["sandbox","live"]` so a typo cannot create a third mode. |
| Migrations | Alembic, autogenerate, `render_as_batch=True` for SQLite. Applied on deploy, verified at startup by `migration_state()`. |
| Secrets | Environment only. `merchant_accounts` stores AES-GCM ciphertext, never plaintext, never returned by any endpoint. |
| Health | `GET /health` — outside the API prefix — reports db reachability, migration state, `paypal_env`, and answers **`503`** when not ready. Never reports "ok" for an unreadable database. |

## 8. What is deliberately not here

No queue. No Redis. No Celery. No microservices. No ORM lazy-loading on the response path (all endpoints return explicit schemas).

Each absence is a decision, not an oversight: a background worker during a three-day build means one more process to keep alive, and the only genuinely asynchronous work — webhook side effects — is small enough to run inline after commit.

## 9. Startup and shutdown

```
startup:  settings load -> engine create -> migration_state()
             "none"/"<revision>" -> log and serve
             "pending"  -> log ERROR, serve anyway (health reports it)
             "error"    -> log CRITICAL, health reports "error"
shutdown: engine.dispose()
```

The app does not refuse to boot on a pending migration. A service that refuses to start is unhealthier than one that starts and reports the truth — and in a demo, "reports the truth" is always better than "is down".
