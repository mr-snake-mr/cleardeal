# Five-Criteria Judging Strategy

Official weights are unknown; equal weighting is used for internal scoring only and never presented as an official result.

The strategy is one line: **each criterion needs evidence a judge can see in the running product.** Not a slide. Not a claim.

## 1 — Technological implementation

They are checking: did you integrate, or did you screenshot an integration?

| Evidence artefact | Where the judge sees it |
|---|---|
| Sandbox order created server-side | Network tab shows no order creation from the browser |
| Capture response with `payee.merchant_id` matching the freelancer (**pending A-2** — the field is unconfirmed) | Payment detail shows the merchant id read from the capture |
| Webhook signature verified, not merely received | Endpoint rejects a replayed event with a tampered body; test shown |
| Idempotent handling | Same event id delivered twice → one payment row |
| AI structured output validated by Pydantic | A malformed response is rejected and surfaced as retry, never stored |
| Automated tests | `pytest` and `npx playwright test` green, shown once |

Earning decisions: amounts come from the database, never the request body; payment state advances only after a verification call; every external failure path has a terminal state.

Losing mistakes: a reachable mock success path; AI output written straight to the database; a webhook handler that trusts the body.

## 2 — Design

They are checking: shipped product or hackathon dashboard?

- Two coherent surfaces — freelancer workspace, client review — not one page with a role toggle.
- Every visible control works. Zero dead buttons on the demo path.
- Empty, loading and error states exist and look deliberate.
- Type and spacing come from tokens.
- The signature screen is unmistakably the centrepiece.

The signature screen:

```
┌──────────────────────────────────────────────────────┐
│ Client request                          v2 approved  │
│ "Can you also add Google login and analytics?"       │
├──────────────────────────────────────────────────────┤
│ Google login                      OUT OF SCOPE       │
│ ├ Cited: §AUTH — "Sign-in is email and password      │
│ │        only. Social sign-in is excluded."          │
│ └ Impact: +4–6 h · +$120 · +2 days                   │
├──────────────────────────────────────────────────────┤
│ Analytics dashboard               NEEDS CLARIFICATION│
│ └ No section addresses reporting — human decision    │
├──────────────────────────────────────────────────────┤
│ [ Send as change proposal ]  Freelancer reviews first│
└──────────────────────────────────────────────────────┘
```

## 3 — Potential impact

- One named persona with numbers ("3–10 projects at $500–$5,000") beats "SMBs".
- Quote real interview lines, or label the hypothesis unvalidated. No invented percentages.
- Show the current workaround cost in measured minutes, not estimates.

## 4 — Innovation

The defensible claim, stated narrowly:

> The agreement is stored as versioned structured data, so every incoming request is checked against the exact approved version, with evidence that resolves to a real section — and the answer becomes an approved artefact, not a chat reply.

Credibility comes from: stable `agreement_sections` ids; the rejection rule (evidence that does not resolve → downgrade to `NEEDS_CLARIFICATION`); version immutability; per-feature classification of mixed requests.

## 5 — Presentation

| Time | Beat | On screen |
|---|---|---|
| 0:00–0:20 | The awkward message | A real scope request in chat |
| 0:20–0:50 | Brief → analysis | Paste; requirements and ambiguities appear |
| 0:50–1:15 | Agreement | Milestones summing to budget; client approves |
| 1:15–1:45 | PayPal | Sandbox checkout, capture, verified record |
| 1:45–2:25 | The scope check | "Add Google login" → cited, classified, priced |
| 2:25–2:45 | Close | Timeline; one sentence |

No architecture diagrams in the video, no file-structure narration, no prerecorded success screens, no fake transactions.

## Scoring discipline

| Criterion | /10 | Evidence | Weakness |
|---|---:|---|---|
| Technology | | | |
| Design | | | |
| Impact | | | |
| Innovation | | | |
| Presentation | | | |

A 10 requires evidence that survives a hostile reviewer. No artefact, no score above 5 — however good the feature feels.
