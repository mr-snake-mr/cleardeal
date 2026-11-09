# ClearDeal

One verifiable record of what was agreed, what changed, and what was paid.

A freelancer's contract is written once, when the client knows least. Every request after that arrives in chat, phrased as conversation. Neither side has a cheap way to ask *"is this in the thing we signed?"*, so the freelancer absorbs the work or has an awkward money conversation with no evidence.

ClearDeal answers that question with a citation, not an opinion.

> **The scope comparison is checked, not believed.** The agreement is stored as versioned structured sections. Every incoming request is classified against one specific approved version, and every citation must resolve to a real `agreement_sections.id` **and** quote text that actually appears in that section. A citation that fails either check is downgraded to `NEEDS_CLARIFICATION` — never quietly accepted.

That rejection rule is the whole product. An AI that reads contracts is not novel; an AI whose claims are mechanically falsifiable is.

## Status — read this before the feature list

**This is a work in progress, and this table is the honest version of it.**

| Area | State |
|---|---|
| Dossier `docs/00`–`docs/17` | complete, audited across two rounds (19 findings → 7 → PASS) |
| Database schema, 14 tables, 8 named + 3 implicit indexes | done, migration reversible |
| Money handling (exact cents, no float) | done, tested across both dialects' type bindings |
| Test suite | **50 tests, 99.05% coverage, `ruff` clean** |
| Auth, projects, AI modules, PayPal, frontend, E2E | **not started** |

So: the foundation is real and tested. The demo is not built yet. Anything below in the present tense describes the design that the foundation exists to support, and `docs/13-SCHEDULE.md` says when each part lands.

## The PayPal position, stated before it is asked

Verified from PayPal's own documentation on 2026-11-09 (`docs/04`):

- A standard Orders v2 integration **cannot** pay a freelancer from a client in one hop unless the freelancer is the merchant of record.
- Multiparty — the correct production answer — **requires PayPal platform approval**, partner enablement, KYC and a bank topology. It cannot ship in three days.
- `"disbursed to a second party"` is not a state the Orders v2 API produces.

Chosen model: **bring-your-own-merchant.** Each freelancer supplies their own sandbox REST credentials; orders are created and captured with *their* credentials, so the money lands in *their* account. No platform fee, no escrow, no payout.

| Claim | Does ClearDeal make it? |
|---|---|
| Orders created and captured server-side against Sandbox | Yes |
| Capture verified with PayPal before a payment is marked paid | Yes |
| Webhook signatures verified; deliveries idempotent | Yes |
| Funds held in escrow | **No** |
| Automatic payouts to freelancers | **No** |
| Platform fee taken | **No** |
| Marketplace payments supported | **No** — sandbox single-merchant; multiparty is the documented roadmap |

Whether the capture response exposes a payee identifier is **unconfirmed** (`docs/16`, A-2). Until a real capture is inspected, the UI shows "Paid — capture verified" and does not claim the money reached a named merchant. That is the difference between evidence and a screenshot.

## Quickstart

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

.\.venv\Scripts\python.exe -m alembic -c alembic.ini upgrade head
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

`-c alembic.ini` is explicit because alembic 1.20 does not reliably discover it under `python -m`.

`GET /health` returns `200` when ready and **`503`** when the database is unreachable or migrations are pending. It is deliberately outside the `/api/v1` prefix.

## What is verified, and what is not

Verified against primary sources: the Orders v2 endpoint set and the states it produces, webhook retry policy (25× over 3 days), the raw-body signing string, both signature-verification methods, the multiparty approval gate and its constraints, Gemini's structured-output constraint stripping, and Gemini pricing.

Unverified, and listed rather than hidden: a second sandbox merchant account (A-1), the payee field (A-2), PayPal idempotency headers (A-3), Gemini's willingness to accept these exact schemas (A-5), webhook delivery to a free-tier host (A-6), live Postgres behaviour (A-7), real email delivery (A-9), market numbers (A-11). Full register with severity and how to close each: [`docs/16`](docs/16-UNVERIFIED-ASSUMPTIONS.md).

## Layout

```
backend/    FastAPI · SQLAlchemy 2.1 · Alembic · SQLite (dev) / Postgres (supported)
frontend/   planned: Vite · React 18 · TypeScript strict · Tailwind 3.4  (Block 3.1)
docs/       00-README → 17-EXECUTION-MODEL, read in that order
AGENTS.md   project rules: stack, commands, gates, stop conditions
```

Read [`docs/00-README.md`](docs/00-README.md) first; it is the index and it states the one decision that is still open.

## What this will never do

- Show a merchant id it did not receive from a verified response.
- Mark a payment paid without a captured, verified PayPal response.
- Let the AI move money, price a milestone, or approve anything.
- Mutate an approved agreement version — a change creates `v(n+1)`.
- Store a model's confidence as a number. `high` / `medium` / `low`, or nothing.
- Pass a test suite whose tests cannot fail.

## Licence

Hackathon submission. Third-party skill repositories used during development are not vendored here; they carry their own licences.
