# Submission

## What this is

A fixed-scope freelance agreement that can settle the question "is this work in
scope?" with an artefact instead of an argument.

The loop: a brief becomes requirements with sources, the requirements become an
agreement whose milestones sum to the budget to the cent, two principals approve
it, a milestone is paid against it, and a later change request is classified
against that version — with every claim carrying a citation that either resolves
to a real section of that version or is refused.

## The one rule everything else serves

> A classification is evidence or it is a guess, and a guess must say so.

`app/services/changes.py::resolve_evidence` is that rule in twenty lines. It
fails in four ways, all tested:

| Way to sound authoritative without having read the agreement | Result |
|---|---|
| Cite a section that does not exist | `NEEDS_CLARIFICATION`, reason names the key |
| Cite a real section with a sentence from a different one | refused |
| Cite a real section with a reworded quote | refused |
| Classify with no citation at all | refused |

A refusal downgrades, it never 500s: a guess the user can see is worth more than
an error that hides it.

## What is proven, and how

| Claim | Evidence |
|---|---|
| Milestones sum to the budget, to the cent | `assert_milestones_sum_to_budget`, wired into the AI client's semantic gate; re-checked at `send` and at `approve` |
| The approved document cannot change underneath anyone | sha256 over rendered sections and money, recomputed on every read (`content_intact`), checked before approval, `409 STALE_VERSION` on a superseded version |
| A payment is only recorded when PayPal proves it | `PayPalClient.capture` asserts order id, order status, capture status, currency, amount, capture id — and refuses two capture objects |
| A webhook cannot be forged | signature over the **raw** body, certificate host pinned to `*.paypal.com`, 5-minute freshness window, `event_id` primary key as the idempotency gate |
| Two signatures need two people | magic-link invitation → passwordless client user → `approve` binds role to principal; the owner gets `409 ROLE_NOT_YOURS` signing as client |
| Money is never a float | integer cents at the AI boundary, `NUMERIC(12,2)` on Postgres, `Decimal` at every service edge, and a strict type that rejects `"10.00"` |

`291 tests, 92.89% coverage, ruff clean`, migrations proven by a full
upgrade → downgrade → upgrade round trip, 14 tables with enforced foreign keys on
SQLite.

## What is not claimed

Stated here because a demo that hides these is the failure this project wrote
seventeen documents to avoid.

1. **No live PayPal transaction has ever been executed.** The client is proven
   against a fake transport and against PayPal's published contract. A-1 (a second
   sandbox merchant) and A-2 (whether the capture response carries a payee — the
   reference documents none) are open. Nothing here has touched a real order.
2. **No live Gemini call has been executed.** The flattened schema is built to
   the documented subset, not observed against the API.
3. **Evidence resolution proves the model agrees with the model.** Section bodies
   are M2's own output; nothing ties a generated section back to a quote from the
   brief. The honest phrasing is "this is what the agreement we generated says"
   (docs/16 A-12).
4. **Negation is not detectable.** Quoting "included" out of "are not included"
   resolves. The quote is shown beside its section so a reader can see it.
5. **No payouts, no escrow, no platform fee, no multiparty.** Multiparty needs
   PayPal partner approval; doc 04 records why.
6. **Deployment is unverified.** `render.yaml` and the Dockerfile exist and have
   never run against Render.

## Where the limits are written down

- `docs/16-UNVERIFIED-ASSUMPTIONS.md` — A-1 … A-12, each with status, impact and
  how to close it. A-11 is closed and says so.
- `docs/13-SCHEDULE.md` — every block, what is done, what is partial, and the
  deliberate deviations (`PATCH /agreements/{id}` is not built; corrections go
  through `v(n+1)`).
- `docs/04-PAYPAL-FEASIBILITY.md` — the merchant model, with the sandbox host
  corrected against PayPal's own reference.

## Run it

```
cd backend
alembic -c alembic.ini upgrade head
uvicorn app.main:app --port 8000
```

Open `/`. `DEMO.md` is the three-minute script, including the two sentences that
have to be said out loud.
