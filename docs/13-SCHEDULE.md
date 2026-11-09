# Three-Day Schedule

Three working days, 10 hours each, one author and one auditor. Every block ends with a **gate**: a named artefact and an audit verdict. A block that ships without its gate is not done, regardless of how good it looks.

Assumption: the demo is recorded on Day 3 evening; submission is the following morning. No work happens in the submission window.

## Day 0 — done (2026-11-09, before the clock)

Dossier 00–17, skill gate established, `git init` before the first file.

**Gate G0 — PASSED.** Dossier audited three times: 14 findings → 6 findings → PASS. Two critical findings were real contradictions between documents, not typos.

## Day 1 — foundations (10 h)

| Block | Work | Artefact |
|---|---|---|
| 1.1 | Repo skeleton, `pyproject.toml`, config, `.env.example`, `main.py`, `/health` | app boots |
| 1.2 | `Money` TypeDecorator, `database.py`, FK pragma, `migration_state()` | `test_money.py`, `test_infrastructure.py` green |
| 1.3 | 14 models, deletion policy, 8 named indexes | `test_schema.py` green |
| 1.4 | Alembic: autogenerate from an **empty** DB, import fix, round-trip | `upgrade → downgrade → upgrade` clean |
| 1.5 | Auth: register, login, session, magic-link exchange | `test_auth.py` |
| 1.6 | Project CRUD + ownership scoping | `test_projects.py` |
| 1.7 | Merchant credential storage, AES-GCM, `PUT/GET /merchants/paypal` | `test_merchants.py` |
| 1.8 | Boundary test (`routers/` may not import `models/`), ruff clean | `test_boundaries.py` |

**Gate G1:** 70+ tests green, coverage ≥ 80%, migration round-trip clean, audit PASS on Tasks 1–5.
**Status (2026-11-09):** 1.1–1.4, 1.7 and 1.8 complete. 1.5 partial — register, login, logout, `me` and signed sessions ship; the magic-link exchange does not. 1.6 partial — create, list and read ship; update and delete do not. Day 2 has started: 2.1 (Gemini client), 2.3 (M1 brief analysis), 2.4 (M2 agreement draft with the `sum(milestones) == budget` invariant in cents) and 2.5 (version persistence, `content_hash`, send / approve / reject). **219 tests, 94.37%, round-trip clean.** 2.8 done — PayPal client: cached token, order create, capture with full verification, payee surfaced only when present. 2.2, 2.6, 2.7 and 2.9 unstarted.

**Audit round on 2.4–2.5:** 10 findings — 1 critical, 5 important, 4 minor. Fixed in the same day: the sum is re-checked at `approve`, the hash is recomputed on every read (`content_intact`) and now covers `due_date`, and a concurrent draft race returns `409 VERSION_RACE` instead of a 500. The critical one — `role` is asserted by the caller, because no client session exists — is **recorded, not fixed**: see doc 16 A-11 and the test that proves it.

**Deviation, deliberate:** `PATCH /agreements/{version_id}` (docs/08 §4) is not implemented. A draft is corrected by drafting `v(n+1)`, which the same contract already provides; a mutable draft path is extra surface for the demo path to trip over. Recorded here rather than quietly omitted.

**Risk on Day 1:** this is the highest-risk day because nothing is demoable at the end of it. Resist the urge to jump to the UI; every later day assumes the schema is right.

## Day 2 — AI and money (10 h)

| Block | Work | Artefact |
|---|---|---|
| 2.1 | Gemini client, prompt files, `ai_runs` logging, one retry | `test_ai_client.py` |
| 2.2 | **A-2 check: one real sandbox capture, inspect the JSON for `payee.merchant_id`** | saved response; updates docs 03/04/08/09 if absent |
| 2.3 | M1 brief analysis + validation | `test_brief_analysis.py` |
| 2.4 | M2 agreement draft + sum invariant | `test_agreement_generation.py` |
| 2.5 | Agreement services: send, approve, version immutability, `content_hash` | `test_agreements.py` |
| 2.6 | Section retrieval (deterministic scoring) + M3 + evidence resolution | `test_scope_classification.py` |
| 2.7 | M4 impact estimate, M5 wording | `test_impact.py` |
| 2.8 | PayPal client: token, order create, capture, verification | `test_paypal_client.py` |
| 2.9 | Webhook: raw body, signature, idempotent insert, reconcile | `test_webhooks.py` |

**Gate G2:** every row in doc 11 §2, §4, §5 has a passing test; `ai_runs` count matches provider calls; a replayed webhook writes zero rows; audit PASS on Tasks 6–10.

**Block 2.2 is not movable.** If the payee field is absent, four documents and one UI component change. Discovering that on Day 3 at 14:00 is how a demo loses its central claim.

## Day 3 — product and demo (10 h)

| Block | Work | Artefact |
|---|---|---|
| 3.1 | Vite + TS + Tailwind + tokens; `lib/api.ts`; error shape | app loads |
| 3.2 | Auth screens, project list, project detail | usable |
| 3.3 | Agreement view, milestone table, sum bar | usable |
| 3.4 | **Scope card** — the signature screen | screenshot before 13:00 |
| 3.5 | Payment: Pay button, SDK, `PaymentStatus`, reconciliation panel | real sandbox capture in the UI |
| 3.6 | Timeline, client review page | usable |
| 3.7 | Empty / loading / error states for every region; a11y sweep | no dead controls |
| 3.8 | Nine E2E scenarios | all green |
| 3.9 | Deploy to HTTPS, real webhook delivery verified | log line |
| 3.10 | Record the 2:45 demo, twice; keep the better take | video |
| 3.11 | README, screenshots, `.env.example`, submission copy | repo ready |

**Gate G3:** nine E2E green, no dead control on the demo path, video recorded, audit PASS on Tasks 11–17.
**Cut line:** if 3.6 slips, the client review page ships read-only (approve disabled with a server-side explanation). If 3.4 slips, nothing else matters — 3.4 is the product.

## Effort estimates, with the honest range

| Area | Estimate | Could be |
|---|---|---|
| Backend foundation (done) | 6 h | — |
| AI modules M1–M5 | 9 h | 13 h if structured output fights the schema |
| Agreement + change services | 7 h | 9 h |
| PayPal integration + webhook | 8 h | 12 h if signature verification misbehaves |
| Frontend | 16 h | 22 h |
| E2E + deploy + video | 8 h | 11 h |
| **Total** | **54 h** | **73 h** |

54 hours of work in 30 hours of clock. The gap is closed by doc 15's cut line, not by optimism. If the estimate is right, the browser UI is the thing that gets cut, not the verified-payment path.

## Gates and who may pass them

| Gate | Owner | May the author self-approve? |
|---|---|---|
| G0 dossier | auditor | No |
| G1 foundation | auditor | No |
| G2 AI + money | auditor | No |
| G3 product + demo | auditor | No |
| Final submission | auditor + a human read-through | No |

An auditor returns PASS only with a quoted artefact per claim. "Looks good" is not a verdict; it is a finding.

## Daily rhythm

```
09:00  gate from yesterday re-checked; today's blocks confirmed
13:00  mid-day audit on completed blocks — findings go into the same day
18:00  all blocks closed or explicitly cut; gate dispatched
19:00  fixes only; no new scope after 18:00
```

The 18:00 rule exists because the failure mode of a three-day build is not running out of time — it is starting one more feature at 21:00 that half-works and replacing something that fully worked.

## What happens if a day is lost

| Lost | Recovery |
|---|---|
| Day 1 | Nothing downstream is possible. Ship the dossier and the schema, state honestly that the build is partial. |
| Day 2 | Drop M5 and M4 ranges to a fixed estimate table; keep M3 evidence binding. The demo keeps its central claim. |
| Day 3 morning | Ship the API with a minimal UI: one page, no navigation. A working scope card beats a pretty shell. |
| Day 3 afternoon | Record the demo against the deployed API using a scripted browser session; keep the video honest about what it shows. |
| Last 2 hours | Freeze. Re-record. Nothing else. |
