# Three-minute demo

Everything below runs against the one page at `/`. Nothing is pre-recorded: the
agreement, the totals and the scope verdict are produced live from the brief.

## Before the clock starts (60 seconds, offline)

```powershell
cd hacko\cleardeal\backend
.\.venv\Scripts\python.exe -m alembic -c alembic.ini upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
# open http://127.0.0.1:8000
```

`.env` needs `SESSION_SECRET`, `CREDENTIALS_ENCRYPTION_KEY` and, for the PayPal
half, `PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET` / `PAYPAL_WEBHOOK_ID` from one
sandbox business account. Without a Gemini key the analysis and drafting steps
return `503` and say nothing was written — do not discover that on stage.

## 0:00 — the problem, in one sentence

> "A client asks for six pages when the agreement says five. Today the freelancer
> either eats it or argues. There is no artefact that settles it."

## 0:20 — the brief becomes requirements with sources

Create the project, press **Analyse brief**.

Point at the table: each requirement is `explicit` or `inferred`, and an
`explicit` one carries the words from the brief that justify it. Say the rule out
loud: *a requirement claimed as explicit without a quote is rejected before it
reaches the table.*

## 0:50 — the draft, and the one number that matters

Press **Draft agreement**.

Point at milestones totalling **$300.00** against a budget of **$300.00**, and at
`content_intact: true`. Say: *the model does not get to check its own arithmetic;
if the milestones miss the budget by a cent the draft is discarded and nothing is
written.*

## 1:20 — two approvals, and an honest caveat

**Send**, then **Approve as freelancer**, then **Approve as client**.

Say, before anyone asks: *in this build the client role is the owner asserting a
role — there is no client session yet, the page says so, and doc 16 A-11 records
it. The mechanism is per-role and per-version; the second principal is the gap.*

## 1:45 — money

Connect the sandbox merchant, press **Create order** on a milestone. Show the
approve link and say: *the amount is read from the milestone row inside the
transaction; the request has no amount field at all. Capture happens server-side
and only a verified PayPal response marks it paid — a 201 with the wrong total
sets `RECONCILIATION_REQUIRED`, never `paid`.*

## 2:15 — the claim

Type: *"Can you also add Google login and throw in the copywriting?"* Press
**Check against the approved scope**.

- Google login: `OUT_OF_SCOPE`, with the quoted sentence and the section it came
  from — resolved server-side to a real section id.
- The copywriting ask: `NEEDS_CLARIFICATION`, confidence `low`, evidence empty,
  with the reason. Point at it and say why: *the section says copywriting is
  **not** included. A substring check cannot see a negation, so the citation is
  refused and the guess is shown rather than dressed up.*

## 2:45 — what is not built

One breath, no hedging:

> "No payouts, no escrow, no platform fee, no multiparty — that needs PayPal
> partner approval, which is in doc 04. Client approval needs a magic-link
> session, which is doc 16 A-11. And nothing here has been captured against a
> live sandbox order yet; the client is proven against a fake transport, not
> against PayPal."

## Fallbacks

- **No Gemini key:** the analysis step 503s with "nothing was written". Show the
  agreement and scope-check screens with a stubbed provider instead — never fake
  a live call.
- **No PayPal credentials:** stop after 1:20. The order-create screen says
  `MERCHANT_NOT_CONNECTED`, which is true and honest.
- **Migrated database:** `git status` clean, `pytest` 280 passing is the
  five-second answer to "is it real".
