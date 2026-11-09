# API Contracts

Base path `/api/v1`. JSON in, JSON out. One error shape everywhere.

Two rules that hold for every endpoint below:

1. **No amount, price, or status ever arrives from the client.** The request may say *which* milestone; it may never say *how much*.
2. **Every mutating endpoint writes exactly one `audit_events` row in the same transaction** as its business write.

## 1. Error shape

```json
{
  "code": "STALE_VERSION",
  "message": "Approval was for v1, but v2 is current.",
  "request_id": "01J9Z0K3T7",
  "details": { "approved_version": 1, "current_version": 2 }
}
```

`code` is a stable SCREAMING_SNAKE string, not a number the frontend has to map. `details` is optional and never contains a secret.

| Code | HTTP | Meaning |
|---|---:|---|
| `VALIDATION_ERROR` | 422 | Pydantic rejected the body; `details.fields` lists offenders |
| `UNAUTHENTICATED` | 401 | No session |
| `FORBIDDEN` | 403 | Session, wrong owner |
| `NOT_FOUND` | 404 | Absent, or not visible to this user |
| `CONFLICT` | 409 | Illegal state transition |
| `STALE_VERSION` | 409 | Approval targeted a superseded version |
| `MILESTONE_SUM_MISMATCH` | 409 | `sum(milestones) != budget`; `details.delta` |
| `PAYMENT_ALREADY_ACTIVE` | 409 | A `CREATED`/`APPROVED` payment exists for this milestone |
| `PAYMENT_STATE_INVALID` | 409 | Capture attempted on a non-capturable payment |
| `MERCHANT_NOT_CONNECTED` | 409 | No merchant credentials bound for this freelancer |
| `INVALID_CREDENTIALS` | 400 | Merchant credentials rejected by PayPal; nothing stored |
| `RECONCILIATION_REQUIRED` | 409 | PayPal disagrees with our record; **not** a retryable error |
| `AI_UNAVAILABLE` | 503 | Provider timeout or 5xx after one retry |
| `AI_INVALID_OUTPUT` | 502 | Model returned unparseable or schema-violating output |
| `PAYPAL_UPSTREAM_ERROR` | 502 | PayPal returned an error; message sanitised |
| `WEBHOOK_SIGNATURE_INVALID` | 400 | Verification failed; nothing changed |
| `RATE_LIMITED` | 429 | Per-user token bucket |

`RECONCILIATION_REQUIRED` is deliberately `409` and not `500`: nothing is broken in the server, the two records disagree, and the UI must show a state rather than an error toast.

## 2. Auth

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/auth/register` | `{email, name, password}` | `201 {user}` + session cookie |
| POST | `/auth/login` | `{email, password}` | `200 {user}` + session cookie |
| POST | `/auth/logout` | — | `204` |
| GET | `/auth/me` | — | `200 {user}` |

Session cookie: `HttpOnly`, `Secure` in non-debug, `SameSite=Lax`. No JWT in `localStorage` — a token readable by JavaScript is a token stealable by any XSS.

**Limitation, stated rather than implied:** sessions are stateless signed tokens, so `POST /auth/logout` clears the cookie but does **not** revoke the token. A cookie captured before logout remains valid until `exp` (12 h by default). Revocation needs a `jti` revocation table, which is a schema change and is not in this build.

### Client access — magic link, no password

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/projects/{project_id}/invitations` | `{email}` | `201 {expires_at}` — token is **emailed, never returned** |
| POST | `/review/exchange` | `{token}` | `200 {session_scope: "review", project_id, version_id}` |
| GET | `/review/agreements/{version_id}` | — | `200` rendered agreement for review |

The invitation token is single-use, expires, and only its SHA-256 is stored. Exchanging it grants a *scoped* session: read one agreement, approve or reject it. It does not grant access to the freelancer's workspace.

## 3. Projects

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/projects` | `{name, client_email, brief, budget_amount, deadline?}` | `201 {project}` |
| GET | `/projects` | — | `200 {items[], total}` |
| GET | `/projects/{id}` | — | `200 {project}` |
| POST | `/projects/{id}/analyse` | `{}` | `200 {requirements[], ambiguities[], risks[], assumptions[]}` |
| POST | `/projects/{id}/agreement` | `{accepted_requirements[]}` | `201 {version}` |

`budget_amount` is a **decimal string**, not a float: `"300.00"`. A JSON number cannot represent every cent exactly, and a payment system that rounds under load is a payment system that eventually pays the wrong amount.

```json
// 422 for budget_amount = "0.00"
{
  "code": "VALIDATION_ERROR",
  "message": "budget_amount must be greater than 0",
  "details": { "fields": { "budget_amount": "must be > 0" } }
}
```

`POST /projects/{id}/analyse` on provider failure returns `503 AI_UNAVAILABLE` and the project **stays `DRAFT`**. Partial analysis is never persisted.

## 4. Agreements

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/agreements/{version_id}` | — | `200 {version, sections[], milestones[]}` |
| PATCH | `/agreements/{version_id}` | `{sections?, milestones?}` | `200 {version}` — `DRAFT` only |
| POST | `/agreements/{version_id}/send` | `{}` | `200 {status: "PENDING_CLIENT"}` |
| POST | `/agreements/{version_id}/approve` | `{role: "freelancer"\|"client"}` | `200 {version}` |
| POST | `/agreements/{version_id}/reject` | `{reason}` | `200 {status: "CANCELLED"}` |

`send` runs the invariant check and refuses on mismatch:

```json
// 409
{
  "code": "MILESTONE_SUM_MISMATCH",
  "message": "Milestones total 280.00 but the budget is 300.00.",
  "details": { "budget": "300.00", "sum": "280.00", "delta": "-20.00" }
}
```

Approval is per-version and immutable:

```json
// approving v1 when v2 is current → 409
{ "code": "STALE_VERSION", "message": "...", "details": { "approved_version": 1, "current_version": 2 } }
```

A `PATCH` against a non-`DRAFT` version is `409 CONFLICT`, not a silent no-op. An approved version is never mutable — a change produces `v(n+1)`.

## 5. Payments

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/milestones/{milestone_id}/payment` | `{}` + `Idempotency-Key` header | `201 {payment_id, paypal_order_id, paypal_client_id, approve_url}` |
| GET | `/payments/{payment_id}` | — | `200 {payment}` |
| POST | `/payments/{payment_id}/capture` | `{paypal_order_id}` | `200 {payment}` |

**`POST /api/v1/payments/{payment_id}/capture` is the single canonical capture route.** There is no second path, no `?action=capture` variant, and no route that marks a payment paid without a verified PayPal response.

### Creation

```
POST /milestones/7f3a.../payment
Idempotency-Key: 7f3a-0001
```

- Amount is read from `milestones.amount` **inside the transaction**. The body has no amount field, so there is nothing to tamper with.
- The same `Idempotency-Key` returns the existing payment with `200`, not a second order. The key is `UNIQUE` in the database, so a race loses at the constraint rather than in application logic.
- A second *live* payment for the same milestone violates `uq_payments_active_per_milestone` → `409 PAYMENT_ALREADY_ACTIVE`.
- No connected merchant → `409 MERCHANT_NOT_CONNECTED`.

`paypal_client_id` is returned because the browser-side JS SDK needs it to render the button. It is a **public** identifier by PayPal's own design — the secret is the other half of the pair and never leaves the server. Returning it is not a disclosure; omitting it would make the frontend depend on a value no endpoint supplies.

### Capture

```
POST /payments/{payment_id}/capture
{ "paypal_order_id": "5O190127TN364715T" }
```

Server sequence, in order, no shortcuts:

1. Payment must be `CREATED` or `APPROVED` → else `409 PAYMENT_STATE_INVALID`.
2. `paypal_order_id` must match the stored one → else `409 CONFLICT`.
3. `POST /v2/checkout/orders/{id}/capture` with the **merchant's** credentials.
4. Assert `status == "COMPLETED"`, amount, currency, and order id. Assert the payee merchant id **only if the response carries one** — its presence is assumption A-2 (16 §1) and has not been observed yet. A missing payee field is recorded in the audit payload and does **not** block the capture; a *mismatched* payee does.
5. Only then: `CAPTURED` + `captured_at` + `paypal_capture_id` + audit event.

Any assertion failure → `409 RECONCILIATION_REQUIRED`, `captured_at` stays `NULL`, the paid UI is blocked. The absent-payee case is deliberately not in that set: making `CAPTURED` unreachable if A-2 turns out false would break the demo path over a cosmetic field. A successful capture whose database write fails converges from the webhook; it is never re-captured.

## 6. Scope checks and change requests

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/projects/{id}/changes` | `{raw_text}` | `201 {change_request, items[]}` |
| GET | `/changes/{id}` | — | `200 {change_request, items[]}` |
| PATCH | `/changes/{id}/items/{item_id}` | `{suggested_cost?, timeline_days?}` | `200 {item}` |
| POST | `/changes/{id}/send` | `{}` | `200 {status: "SENT"}` |
| POST | `/changes/{id}/approve` | `{role}` | `200 {new_version}` |
| POST | `/changes/{id}/reject` | `{reason}` | `200 {status: "REJECTED"}` |

```json
// 201 response, one item
{
  "feature": "Google login",
  "classification": "OUT_OF_SCOPE",
  "explanation": "The approved auth section limits sign-in to email and password and explicitly excludes social sign-in.",
  "confidence": "high",
  "evidence": [
    { "section_key": "AUTH", "section_id": "9c1e...", "quote": "Sign-in is email and password only. Social sign-in is excluded." }
  ],
  "effort_low_h": 4,
  "effort_high_h": 6,
  "suggested_cost": "120.00",
  "timeline_days": 2
}
```

`section_id` is present in the response and was **produced by the server**, not the model. The model returned `section_key` and `quote`; the service resolved the key and asserted the quote is a substring. If either step fails the item comes back as:

```json
{ "classification": "NEEDS_CLARIFICATION", "confidence": "low", "evidence": [], "suggested_cost": null }
```

`ALREADY_INCLUDED` items always carry `suggested_cost: null`. Charging for work already sold is the failure mode this product exists to prevent; the API makes the field unavailable rather than defaulting it to zero.

Approving a change:
- creates `v(n+1)` with `status = 'DRAFT'` to begin with,
- sets the previous version to `SUPERSEDED`,
- **does not touch any `payments` row** — captured money is immutable.

## 7. Timeline

| Method | Path | Returns |
|---|---|---|
| GET | `/projects/{id}/timeline` | `200 {items[]}` ordered `created_at ASC, id ASC` |

```json
{ "type": "PAYMENT_CAPTURED", "actor": "system:paypal", "at": "2026-11-11T09:14:03Z",
  "payload": { "milestone": "Design", "amount": "100.00", "capture_id": "3C6..." } }
```

The tiebreaker `id ASC` matters: two events in the same second must have a stable order, or the timeline reshuffles between reloads and looks untrustworthy on camera.

## 8. Webhook

| Method | Path | Auth | Returns |
|---|---|---|---|
| POST | `/webhooks/paypal` | PayPal signature | `200` always once verified |

```
raw = await request.body()          # BEFORE any parsing
verify(raw, headers)                # CRC32 + RSA-SHA256 against cached cert
  invalid -> 400, log transmission_id, change nothing
INSERT webhook_events(event_id) ON CONFLICT DO NOTHING
  conflict -> 200 immediately (idempotent)
reconcile capture_id against payments
  200
```

Subscribed events: `PAYMENT.CAPTURE.COMPLETED`, `PAYMENT.CAPTURE.DENIED`, `PAYMENT.CAPTURE.REFUNDED`, `CHECKOUT.ORDER.APPROVED`.

A `2xx` is returned only after verification. Returning `200` to an unverified body would make this endpoint a way to mark invoices paid.

## 9. Merchant credentials

| Method | Path | Body | Returns |
|---|---|---|---|
| PUT | `/merchants/paypal` | `{client_id, client_secret}` | `201 {status, paypal_merchant_id?}` |
| GET | `/merchants/paypal` | — | `200 {status, paypal_merchant_id, connected_at}` |
| DELETE | `/merchants/paypal` | — | `204` |

The response contains **no secret field at all** — not masked, not truncated, absent. A masked secret is still a leak of its length and prefix.

On `PUT`, the server obtains a token with the supplied credentials to prove they work, then stores them. Invalid credentials → `400 INVALID_CREDENTIALS`, nothing stored.

`paypal_merchant_id` is populated **if PayPal exposes it** during that token exchange; if it does not, the column stays `NULL` until the first verified capture supplies a payee. That is assumption A-13, not a documented capability — the only payee-identifier claim in 04 is about the capture response (A-2), and this endpoint does not get to assume more than the source document supports.

## 10. Health

| Method | Path | Returns |
|---|---|---|
| GET | `/health` | `200` or `503` |

```json
{ "status": "ok", "db": "ok", "migrations": "190a4bb0fc86", "version": "0.1.0", "paypal_env": "sandbox" }
```

`migrations` reports `"pending"` (no revision table), `"none"` (table present, empty), `"<revision>"`, `"error"` (the table exists but cannot be read), or `"unknown"` (the database itself is unreachable). The distinction exists because treating an unreadable database as "pending" sends an operator to run migrations during an outage instead of investigating it.

**The status code is `503` whenever `status` is `degraded`** — that is, when the database is unreachable or migrations are `pending`. A probe that answers `200` while the database is down keeps an orchestrator routing traffic to a service that cannot serve it. `status` and `db` carry the detail; the code carries the decision.

The probe is mounted at **`/health`**, outside the `/api/v1` prefix, because it is infrastructure rather than part of the public API contract. `/api/v1/health` returns `404`.

## 11. Idempotency summary

| Operation | Guard |
|---|---|
| Create payment | `Idempotency-Key` header, `UNIQUE` in DB |
| One live payment per milestone | partial unique index |
| Capture | `paypal_capture_id` `UNIQUE`; state must be capturable |
| Webhook delivery | `webhook_events.event_id` primary key |
| Approve version | version must be `PENDING_CLIENT`; `STALE_VERSION` otherwise |
| Approve change | status must be `SENT` |

Every guard is a database constraint or an explicit state check. None of them is "the frontend disables the button".
