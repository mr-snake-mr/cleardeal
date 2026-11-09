# Product Requirements Document — ClearDeal

## 1. Objective

Give a freelancer and a client one verifiable record of three things: what was agreed, what changed, and what was paid.

**Success looks like:** a client types a vague brief; ClearDeal produces a scoped agreement whose milestones sum to the budget; both parties approve one immutable version; the client pays a milestone through PayPal Sandbox; later the client asks for "Google login and analytics"; ClearDeal classifies each feature against the approved version, quotes the section that excludes it, and produces v2 — without touching the captured payment.

**Primary user:** solo freelance developer or designer, 3–10 concurrent projects, $500–$5,000 each, coordinating over chat and email.
**Secondary:** the client paying them — a founder or small-business owner buying one project.

## 2. The problem, stated precisely

The failure is not "no contract". Templates are free. The failure is that **the contract does not stay true**.

1. The agreement is written once, when the client knows least.
2. Requests arrive later, in chat, phrased as conversation.
3. Neither party has a cheap way to ask "is this in the thing we signed?"
4. The freelancer absorbs the work or has an awkward money conversation with no evidence.
5. Payments are disconnected from the milestones they were for.

Existing tools fix pieces: contracts get signed, work gets sold, money moves. Nothing joins an approved versioned scope to an AI comparison of each new request, and then to the payment record.

## 3. Jobs to be done

| Job | When | Today's workaround |
|---|---|---|
| Turn a messy brief into something signable | Start | Copy a template, edit 40 minutes |
| Tell me if this request is extra work, with proof | Mid-project | Memory, screenshots, argument |
| Show me what I am paying for | Before paying | Ask again, hope |
| Prove what we agreed | Dispute | Scroll chat history |

## 4. Scope

### In
Project creation from natural language · AI requirement extraction with visible provenance · agreement generation with milestones summing exactly to budget · immutable versions and hash-bound dual approval · milestone payment via PayPal Sandbox · server-side capture with verification · AI scope comparison with cited evidence · change proposal → approval → v(n+1) · audit timeline.

### Out
Marketplace payouts/escrow · platform fees · real money/KYC · chat, task boards, time tracking · file storage · multicurrency (v1 is USD only) · mobile apps · tax/invoicing · refunds UI.

## 5. User stories with acceptance criteria

**F-1 Create project** — given a brief and budget of 300 USD, then a project exists owned by me in `DRAFT`. Given budget 0 or negative, then `422` and no project.

**F-2 Analyse** — given a brief, then I see requirements, ambiguities, risks, assumptions, each marked `explicit` or `inferred`. Given the provider is down, then the project stays `DRAFT`, the error is shown, a retry is offered, and nothing partial is saved.

**F-3 Generate agreement** — given accepted requirements and budget, then a draft exists with at least one milestone and `sum(milestone.amount) == budget`. Given a mismatch, then send is blocked and the delta is shown.

**F-4 Mutual approval** — given a draft, when sent, v1 becomes `PENDING_CLIENT` and an invitation is issued. Client approval stamps **v1 specifically**. Given v2 exists, a v1 link returns `409 STALE_VERSION`.

**F-5 Milestone payment** — given an approved version, requesting payment creates an intent whose amount comes from the server, never the request. Payment becomes `CAPTURED` only after the capture is verified against PayPal. A duplicate webhook produces exactly one financial row.

**F-6 Scope comparison** — given an approved v2, a request for "Google login" returns `OUT_OF_SCOPE` with at least one cited section of v2, effort, cost and timeline impact. A repeated requirement returns `ALREADY_INCLUDED` with null cost. A mixed request is classified per feature.

**F-7 Change approval** — freelancer edits and sends; client approves or rejects; on approval v3 exists, v2 is preserved, future milestones update, captured payments are untouched.

**F-8 Audit trail** — ordered events with actor and timestamp.

## 6. Commands

```
Backend  : .venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
Migrations: .venv\Scripts\python.exe -m alembic -c alembic.ini upgrade head
Frontend : npm run dev
Test     : .venv\Scripts\python.exe -m pytest    |    npm run test    |    npx playwright test
Lint     : .venv\Scripts\python.exe -m ruff check .
```

`-c alembic.ini` is explicit because alembic 1.20 does not auto-discover the file reliably under `python -m`.

## 7. Code style

Python: type hints on every public function, Pydantic at the boundary, business logic in `services/` never in `routers/`. TypeScript: no `any`, explicit prop interfaces, all fetches through `lib/api.ts`.

## 8. Boundaries

- **Always:** validate at the boundary, sum-check every agreement, verify a capture before marking paid, log security events, run tests before commit.
- **Ask first:** schema changes, new dependencies, payment-flow changes, CORS changes.
- **Never:** commit secrets, log tokens, trust a client-supplied amount, mutate a captured payment, let AI move money.

## 9. Success criteria

1. All nine E2E scenarios pass against real Sandbox and a real model call.
2. `sum(milestones) == budget` on every version — enforced in the transaction, re-checked by a property test, audited by a CI query.
3. A replayed webhook creates zero new rows.
4. A tampered frontend amount is ignored.
5. Every AI classification in the demo resolves its evidence to a real section id.
6. No dead control in the demo.

## 10. Decisions

1. **Merchant model** — bring-your-own-merchant (04 §2, Model B). Blocking check: A-1.
2. **Currency** — decided: USD only, enforced by `CHECK (currency IN ('USD'))`.
3. **Client identity** — magic link, no password.
