# Risk Register

Scored 1–5 on likelihood and impact. Anything with impact 5 and likelihood ≥ 3 gets a named mitigation and an explicit trigger — a risk without a trigger is a worry, not a control. In this build the author and auditor roles from [17](17-EXECUTION-MODEL.md) are the standing owners of every mitigation, so ownership is not repeated per row.

## Top of register

| # | Risk | L | I | Score | Trigger | Mitigation |
|---|---|---:|---:|---:|---|---|
| R-1 | Multiparty payments need PayPal approval and cannot ship | 5 | 3 | 15 | Day 0 finding | Model B (bring-your-own-merchant) shipped instead; C documented as the production path |
| R-2 | Capture response does not expose `payee.merchant_id` (A-2) | 3 | 5 | 15 | Block 2.2, one real capture | Four documents and one UI component drop the payee claim; product still works, wording changes |
| R-3 | Gemini rejects the schema or returns unvalidated output | 3 | 4 | 12 | First M3 call | Shallow schema, Pydantic second layer, one retry, `AI_INVALID_OUTPUT` surfacing |
| R-4 | Storing freelancers' PayPal secrets | 5 | 4 | 20 | Model B chosen | AES-GCM at rest, narrow scopes, never logged, never returned; production answer is OAuth seller onboarding |
| R-5 | Webhook signature verification is fiddly (raw body, cert URL) | 4 | 4 | 16 | First delivery | Self-verify against cached cert, raw bytes captured first, simulator events excluded from the demo |
| R-6 | 54 h of work does not fit in 30 h | 4 | 4 | 16 | Day 2 18:00 gate | Doc 15 cut line: cut the UI, never the verified-payment path |
| R-7 | Only one sandbox business account available | 3 | 4 | 12 | Sandbox checklist §6 | Model A behind one config switch; demo shows a single merchant and says so |
| R-8 | Network or provider failure during the demo | 3 | 4 | 12 | Dry run | Local recording, replay flag that is visible on screen, no silent fallbacks |
| R-9 | Single DB role, no row-level security | 5 | 3 | 15 | Accepted for the build | Named production gap; app-level authorisation is tested per endpoint |
| R-10 | No `CREDENTIALS_ENCRYPTION_KEY` rotation path | 5 | 2 | 10 | Accepted | Startup refuses if key is missing while merchant rows exist; rotation is a documented TODO |
| R-11 | Workspace deleted again (it happened once) | 2 | 5 | 10 | Before the first commit of a session | `git init` before the first file; commit at every task boundary; verified at 18:32 on 09 Nov that a repo now exists |
| R-12 | Judge reads this as a ChatGPT wrapper | 4 | 3 | 12 | Any AI question | The evidence-resolution rule is demonstrable in one screen; the rejection is shown, not described |
| R-13 | Frontend overruns, leaving no time for E2E | 4 | 3 | 12 | Day 3 13:00 | Signature screen first (3.4), everything else after; navigation cut before correctness is cut |
| R-14 | AI latency makes the scope check feel broken | 3 | 2 | 6 | First end-to-end run | 3s budget per call, skeleton states, retrieval narrowed to top-8 sections |
| R-15 | SQLite write contention during the demo | 1 | 3 | 3 | Two browser windows | WAL mode, single demo user, Postgres path documented |

## R-4 in full — the one that decides whether this is responsible

Model B requires storing a freelancer's PayPal REST `client_secret`. That is a real liability, not a detail.

| Control | Implementation |
|---|---|
| Encryption at rest | AES-GCM, 96-bit random nonce per record, ciphertext in `merchant_accounts` |
| Key handling | `CREDENTIALS_ENCRYPTION_KEY` from environment only; absent key + existing rows = refuse to start |
| Never returned | `GET /merchants/paypal` has no secret field at all — not masked, absent |
| Never logged | logging filter redacts `secret`, `token`, `authorization` |
| Narrow scope | only order create, order read, order capture. No Payouts, no Refunds, no account endpoints. |
| Revocable | user can `DELETE /merchants/paypal`; status → `REVOKED`, orders blocked |

The honest framing for a judge: *"Multiparty onboarding requires PayPal partner approval, so this is the only model in which the money actually reaches the freelancer. Production replaces the pasted credential with OAuth seller onboarding."* That sentence is in doc 04 and should be in the README.

## R-2 in full — the assumption that can invalidate a claim

Everything about "the money reached the freelancer" rests on the capture response carrying a payee identifier. A-2 is unconfirmed.

```
If the field is present:  display "Paid to (PayPal-verified)" + merchant id  →  evidence
If the field is absent:   display "Paid — capture verified"                  →  honest, weaker
```

Under no circumstance is the absent case papered over with an unverified claim. The failing move — showing a merchant id we did not receive — would be "fake transaction data", the exact thing doc 03 §5 lists as a losing mistake.

## R-11 in full — the risk that already materialised

At ~18:32 on 2026-11-09 the working folder, the five cloned skill repositories and the skill catalog were deleted by an unidentified process. Recycle Bin empty, no shadow copies, no File History, no restore points, **and no git repository** — so nothing was recoverable.

What changed:

1. `git init` runs before the first file is written.
2. A commit at every task boundary, not at the end of the day.
3. The audit gate checks `git log` is non-empty before a task can pass.
4. `AGENTS.md` §6 makes this non-negotiable rather than advisory.

What did **not** change: the cause is still unknown. The most that can be said is that an elevated cleanup script ran shortly before; inspecting it showed it clears system caches and does not target this folder. That is not a cause, and no cause will be invented to fill the gap.

## Watch list (not scored, but tracked)

| Item | Note |
|---|---|
| Email delivery for invitations | stubbed; the review link is copied by hand (A-9) |
| Postgres untested | supported by construction only (A-7) |
| No pen test | stated as a limitation, not mitigated |
| Client review page approve action | depends on magic-link session scope working before 13:00 on Day 3 |
| Screen recording quality | a bad take costs 30 minutes; record twice |

## Escalation

| Situation | Action |
|---|---|
| Any impact-5 risk triggers | Stop feature work. Fix or re-scope that day. |
| Two impact-4 triggers in one day | Cut to doc 15's minimum shippable and protect the demo path. |
| Payment path breaks after 18:00 on Day 3 | Do not repair live. Record the demo against the last known-good build and state the current state in the submission notes. |
| Data loss again | Restore from the last commit; if there is no commit, report the loss honestly rather than reconstructing from memory and calling it the same thing. |

The last row is the rule that came out of the incident. A reconstruction that claims to be the original is a lie about provenance, and provenance is the whole product.
