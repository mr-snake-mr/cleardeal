# Unverified Assumptions

The purpose of this file is to make it impossible to confuse what was **checked** with what was **assumed**. Every claim in this dossier is either sourced from a primary document or listed here.

Rule for the build: an assumption that turns out false must change the documents and the UI, not be quietly dropped.

## 1. Blocking assumptions

### A-2 — The capture response exposes a payee identifier

| | |
|---|---|
| **Assumption** | `POST /v2/checkout/orders/{id}/capture` returns a capture whose `payee.merchant_id` identifies the receiving merchant |
| **Status** | **Unconfirmed.** PayPal's Orders v2 reference documents a `payee` object on captures; the exact field name and its presence on a sandbox capture have not been observed in this build |
| **How to close** | Block 2.2: create and capture one real sandbox order, save the JSON, grep for `payee` |
| **Time** | 20 minutes |
| **If false** | Docs 03, 04, 08, 09 lose the "paid to (PayPal-verified)" claim. The UI shows "Paid — capture verified". Everything else stands. |
| **Severity** | High. This is the only assumption that can change what the product says about money. |

### A-1 — A second sandbox business account is available

| | |
|---|---|
| **Assumption** | PayPal provisions (or allows creation of) a second sandbox business account, so a freelancer and a client can be distinct merchants |
| **Status** | Unconfirmed. Docs 04 §6 records the checklist; the accounts have not been inspected |
| **How to close** | Developer dashboard → Sandbox → Accounts, take a screenshot |
| **If false** | Model B collapses to Model A (platform as merchant of record) with the honest caveat, or the demo shows a single merchant and says so on screen |
| **Severity** | High — decides the merchant model |

### A-3 — Order creation supports an idempotency header

| | |
|---|---|
| **Assumption** | PayPal accepts `PayPal-Request-Id` (or equivalent) on `POST /v2/checkout/orders` |
| **Status** | Unconfirmed |
| **Fallback already implemented** | Clearing reuses an existing `CREATED` order for the same milestone rather than creating a second, and `payments.idempotency_key` is `UNIQUE`. Correctness does not depend on the header. |
| **Severity** | Low — a convenience, not a control |

## 2. Provider assumptions

### A-4 — Gemini model availability and free-tier limits

| | |
|---|---|
| **Assumption** | Gemini 3.8 Flash is callable on the free tier at the volume a demo needs (≈60 calls/day) |
| **Verified** | Pricing page states \$0.75/\$3.75 per 1M tokens and a free tier, as of 2026-11-09 |
| **Unverified** | Per-minute request limits and whether the free tier is throttled during the demo window |
| **If false** | Two API keys, one per module group; or pay-as-you-go — a full demo run costs under \$0.02 (05 §7) |
| **Severity** | Low |

### A-5 — Gemini accepts this schema subset

| | |
|---|---|
| **Verified** | `minItems`/`maxItems` are enforced; `minLength`, `maxLength`, `minProperties`, `maxProperties`, `uniqueItems`, `contains` are **silently stripped**; complex schemas can return `400 INVALID_ARGUMENT` |
| **Unverified** | Whether the specific M2/M3 schemas are simple enough to avoid the 400. This is discovered by trying, not by reading |
| **Mitigation already designed** | Generation schema kept shallow; every length/uniqueness rule re-implemented in Pydantic, because a rule that exists only in the schema does not exist |
| **Severity** | Medium — a 400 here would cost 2–4 hours on Day 2 |

### A-6 — Render free tier can receive a PayPal webhook

| | |
|---|---|
| **Assumption** | An HTTPS endpoint on port 443 that stays reachable — including after idle sleep — for a real PayPal delivery |
| **Verified** | PayPal requires HTTPS on 443 and rejects delivery to localhost |
| **Unverified** | Whether a free-tier instance sleeping on idle causes the first delivery to time out. PayPal retries 25× over 3 days, so a cold start is survivable but the *first* verification attempt may not be the one observed |
| **How to close** | Deploy early (Block 3.9, not 3.10), send a real event, watch the log |
| **Severity** | Medium |

### A-7 — Postgres behaviour

| | |
|---|---|
| **Assumption** | `Money` produces `NUMERIC(12,2)`, batch mode is a no-op, and the partial indexes work |
| **Verified** | `str(Money().load_dialect_impl(PG)) == "NUMERIC (12, 2)"` is asserted in the test suite |
| **Unverified** | Anything requiring an actual Postgres server: FK behaviour, index usage, concurrent writes |
| **How to close** | `docker compose up postgres`, point `DATABASE_URL` at it, run the suite. Half a day, not scheduled |
| **Severity** | Low for the submission, high if it were deployed |

## 3. Product assumptions

### A-8 — A client will approve through a magic link without an account

| | |
|---|---|
| **Assumption** | Removing registration for the paying party increases approval completion |
| **Status** | Reasoned, not measured. No user has been observed doing this |
| **If false** | Approval requires a password, which raises friction on the exact step the demo depends on |
| **Severity** | Medium |

### A-9 — Email delivery is a stub

| | |
|---|---|
| **Status** | True limitation. Invitation tokens are generated and stored correctly; delivery is not wired to a real provider |
| **Consequence** | The client review link is copied by hand for the demo |
| **How to close** | One provider sandbox key and a 20-line sender |
| **Severity** | Low for judging, medium for honesty — the review page must be reachable in the demo, so the link is copied rather than emailed |

### A-10 — Judging criteria weights

| | |
|---|---|
| **Assumption** | Five criteria, roughly equal weight |
| **Status** | Unknown. Doc 03 uses equal weighting for internal scoring only and states that it is not an official result |
| **Severity** | Low — the strategy is "evidence per criterion" regardless of weights |

### A-11 — Market and persona numbers

| | |
|---|---|
| **Assumption** | Solo freelancers run 3–10 concurrent projects at \$500–\$5,000 |
| **Status** | **Unvalidated.** No interviews were conducted. The numbers in doc 01 and doc 02 are a working hypothesis and are labelled as one |
| **What is forbidden** | Presenting these as survey results, quoting invented interview lines, or inventing a percentage of freelancers who "lose money to scope creep" |
| **Severity** | Medium for the impact criterion — a judge who asks "where is that from?" must get "that is our hypothesis, here is how we would test it" |

### A-12 — Sandbox latency is compatible with a 2:45 demo

| | |
|---|---|
| **Assumption** | Token fetch + order create + capture complete inside a few seconds on demo-day network |
| **Status** | Plausible, unmeasured |
| **Mitigation** | Warm the sandbox with a throwaway transaction 10 minutes before recording; budgets of 5s token / 10s capture |
| **Severity** | Low, but it is the kind of thing that ruins a take |

## 4. Verified, for contrast

These were checked against primary sources and are not assumptions:

| Claim | Source |
|---|---|
| Orders v2 endpoint set and the three states it produces | [developer.paypal.com/api/orders/v2](https://developer.paypal.com/api/orders/v2/) |
| Webhook retry 25× / 3 days, raw-body signing string, two verification methods, simulator limitation | [developer.paypal.com/api/rest/webhooks/rest](https://developer.paypal.com/api/rest/webhooks/rest/) |
| Multiparty needs approval; partner fees need enablement; fee currency = transaction currency; platform fee unsupported for first-party | [developer.paypal.com/platforms](https://developer.paypal.com/platforms/) |
| Payouts v1 requires a funding account the platform holds | [developer.paypal.com/api/payments.payouts-batch/v1](https://developer.paypal.com/api/payments.payouts-batch/v1/) |
| Gemini structured-output constraint stripping | [ai.google.dev/gemini-api/docs/structured-output](https://ai.google.dev/gemini-api/docs/structured-output) |
| Gemini pricing | [ai.google.dev/gemini-api/docs/pricing](https://ai.google.dev/gemini-api/docs/pricing) |
| SQLite requires `PRAGMA foreign_keys=ON` per connection | SQLite documentation, and `test_foreign_key_enforcement_is_actually_on` |
| Alembic silently skips indexes containing expressions | Observed in this build: three declared indexes absent from `PRAGMA index_list` until `desc()` was removed |

## 5. Closing log

Maintained as assumptions get resolved. Empty until Block 2.2 runs.

| # | Closed on | Result | Documents changed |
|---|---|---|---|
| — | — | — | — |

An assumption is only closed by an observation with a saved artefact. "It should work" closes nothing.
