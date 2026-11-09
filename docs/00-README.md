# ClearDeal — Phase 0 / Phase 1 Dossier

**Status:** all 18 documents delivered. Awaiting approval on the merchant model only.
**Rebuilt:** 2026-11-09 after an unexplained workspace deletion. See §*What happened*.
**Target:** PayPal AI Hackathon

### Current state

| Area | State | Evidence |
|---|---|---|
| Dossier 00–17 | complete | this directory |
| Backend foundation | Tasks 1–2 done | 48 tests, 99.04% coverage |
| Schema + migration | done | 14 tables, 11 indexes; `upgrade → downgrade → upgrade` clean |
| Auth, projects, AI, PayPal, frontend | not started | Day 1 blocks 1.5+ per [13](13-SCHEDULE.md) |

Definition of done: [15 §7](15-MVP-SCOPE.md). Open assumptions: [16](16-UNVERIFIED-ASSUMPTIONS.md) — A-1 and A-2 are the two that can change what the product is allowed to say.

## Read in this order

| # | Document | Answers |
|---|---|---|
| 01 | [PRD](01-PRD.md) | What we build, for whom, what "done" means |
| 02 | [Competitive analysis](02-COMPETITIVE-ANALYSIS.md) | Who already does this, and the gap we own |
| 03 | [Judging strategy](03-JUDGING-STRATEGY.md) | What evidence each criterion needs |
| 04 | [PayPal feasibility](04-PAYPAL-FEASIBILITY.md) | **The blocking decision.** What PayPal will and will not do |
| 05 | [AI architecture](05-AI-ARCHITECTURE.md) | Five modules, structured output, anti-hallucination |
| 06 | [System architecture](06-SYSTEM-ARCHITECTURE.md) | Modules, boundaries, deployment |
| 07 | [Database schema](07-DATABASE-SCHEMA.md) | Tables, constraints, state machines |
| 08 | [API contracts](08-API-CONTRACTS.md) | Endpoints, errors, idempotency |
| 09 | [Frontend structure](09-FRONTEND-STRUCTURE.md) | Routes, components, design tokens |
| 10 | [Security threat model](10-SECURITY-THREAT-MODEL.md) | STRIDE per boundary |
| 11 | [Edge cases](11-EDGE-CASES.md) | Every failure the build must survive |
| 12 | [Testing strategy](12-TESTING-STRATEGY.md) | Pyramid, nine scenarios, evidence rules |
| 13 | [Schedule](13-SCHEDULE.md) | Three days, hour blocks, gates |
| 14 | [Risk register](14-RISK-REGISTER.md) | What kills this, and the mitigation |
| 15 | [MVP scope](15-MVP-SCOPE.md) | In, out, cut line |
| 16 | [Unverified assumptions](16-UNVERIFIED-ASSUMPTIONS.md) | What I could not confirm |
| 17 | [Execution model](17-EXECUTION-MODEL.md) | Author / Auditor split, skill mandate |

Project rules live in [`../AGENTS.md`](../AGENTS.md).

## The one decision still open

**PayPal merchant model.** Verified from PayPal's own docs (04): a standard Orders v2 integration cannot pay a freelancer from a client in one hop unless the freelancer is the merchant of record, and multiparty requires PayPal approval.

Recommendation: **bring-your-own-merchant** — each freelancer supplies their own sandbox REST credentials, so the client's money lands in the freelancer's account. No platform fee, no payout, no escrow. Whether the capture response exposes a payee identifier is **unconfirmed** (A-2) and is the first thing to check on Day 2.

## The honest position on PayPal

| Claim | Permitted? |
|---|---|
| Orders created and captured server-side against Sandbox | Yes |
| Capture verified with PayPal before marking paid | Yes |
| Webhook signatures verified, events idempotent | Yes |
| Funds held in escrow | **No** |
| Automatic payouts to freelancers | **No** |
| Platform fee taken | **No** |
| Marketplace payments supported | **No** — say "sandbox single-merchant, multiparty is roadmap" |

## Verified against official sources

- Orders v2 endpoints — [developer.paypal.com/api/orders/v2/](https://developer.paypal.com/api/orders/v2/)
- Webhook receipt, retry (25× / 3 days), verification, raw-body rule — [developer.paypal.com/api/rest/webhooks/rest/](https://developer.paypal.com/api/rest/webhooks/rest/)
- Multiparty / Complete Payments Platform, partner fees, approval gate — [developer.paypal.com/platforms/](https://developer.paypal.com/platforms/)
- Payouts v1 endpoints — [developer.paypal.com/api/payments.payouts-batch/v1/](https://developer.paypal.com/api/payments.payouts-batch/v1/)
- Gemini structured output + the constraints it silently drops — [ai.google.dev/gemini-api/docs/structured-output](https://ai.google.dev/gemini-api/docs/structured-output)
- Gemini pricing (3.8 Flash $0.75/$3.75 per 1M to 2026-12-31; 4 Argon $2/$10) — [ai.google.dev/gemini-api/docs/pricing](https://ai.google.dev/gemini-api/docs/pricing)

## What happened to this folder

The workspace was deleted by an unidentified process at ~18:32 on 2026-11-09: `cleardeal/`, the five cloned skill repositories, and the skill catalog all disappeared. Recycle Bin was empty; there were no shadow copies, no File History and no restore points. **There was no git repository, so nothing was recoverable.**

Two process changes came out of it:

1. `git init` runs before the first file, and commits happen at every task boundary.
2. This dossier is a reconstruction. Content is faithful to the audited version, but line-for-line identity is not claimed.

## Repository layout

```
cleardeal/          <- git repo, reproducible submission
  docs/             <- this dossier
  backend/          <- FastAPI + SQLAlchemy + Alembic
  frontend/         <- React + Vite + TS
```

The five cloned skill repos are **not** part of this repo — they carry third-party licences.
