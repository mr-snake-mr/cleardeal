# Unverified Assumptions

The purpose of this file is to make it impossible to confuse what was **checked** with what was **assumed**. Every claim in this dossier is either sourced from a primary document or listed here.

Rule for the build: an assumption that turns out false must change the documents and the UI, not be quietly dropped.

## 1. Blocking assumptions

### A-2 — The capture response exposes a payee identifier

| | |
|---|---|
| **Assumption** | `POST /v2/checkout/orders/{id}/capture` returns a capture whose `payee.merchant_id` identifies the receiving merchant |
| **Status** | **Unconfirmed, and now doubted.** The capture reference (`orders-capture`, fetched 2026-11-09) documents **no `payee` on the capture object**; `payee` appears in the *create-order request* body and in `payment_instruction.platform_fees`, neither of which is the capture response. A live sandbox response may still carry it — a documentation omission is not a guarantee — but the earlier wording here ("the reference documents a payee object on captures") was wrong and is corrected |
| **How to close** | Block 2.2: create and capture one real sandbox order, save the JSON, grep for `payee`. `app/services/paypal.py::_extract_payee` surfaces the id when present and returns `None` when absent, so either outcome is a data change, not a code change |
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

### A-13 — Storing merchant credentials yields a merchant id

| | |
|---|---|
| **Assumption** | Exchanging a freelancer's `client_id` / `client_secret` for a token exposes their PayPal merchant id, so `merchant_accounts.paypal_merchant_id` can be filled at connect time |
| **Status** | **Unconfirmed.** Nothing in 04 documents this: the only payee-identifier claim in the feasibility report concerns the *capture* response (A-2), not the token exchange |
| **How to close** | Same session as A-2: inspect the token response and the first capture; if neither carries an id, the column stays `NULL` |
| **If false** | `merchant_accounts.paypal_merchant_id` is populated from the first verified capture instead, and the connect screen simply does not show an id. 08 §9 is written to allow both. |
| **Severity** | Low for the build, medium for wording — it must not be described as "we verify the merchant at connect time" |

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

### A-14 — Incumbents do not store the agreement as structured data

| | |
|---|---|
| **Assumption** | HoneyBook, Bonsai, Dubsado and similar tools treat the contract as a signed document, not as versioned structured sections that can be queried |
| **Status** | **Unverified.** No incumbent product was opened and inspected during this build; doc 02's market table is assembled from public feature pages, which do not disclose storage design |
| **How to close** | One trial account, one contract created, one API or export inspected. Half a day. |
| **If false** | The gap narrows from "nobody does this" to "we do the citation verification better". The honest framing in doc 02 already allows for that; doc 03's innovation claim would need tightening, not abandoning |
| **Why it is listed** | Because "no one else does this" is the most tempting unverified claim in any pitch, and it is the easiest for a judge to falsify from a laptop |

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

### A-16 — "Verified" means the quote matches the draft, not the client's words

| | |
|---|---|
| **Assumption** | A citation that resolves to a stored section proves the classification |
| **Status** | **Partly false.** `agreement_sections.body` is M2's own output, so resolution proves the model agrees with the model. Nothing ties a generated section to a brief quote the way `Requirement.explicit` is tied to one. The claim it supports is "this is what the agreement we generated says", not "this is what the client asked for" |
| **Also unprovable by substring** | Quoting `"included"` out of `"are not included"` reverses the meaning and still resolves; negation is not a boundary problem. The UI shows the quote beside the section so a reader can see it |
| **Also lost at persistence** | `ResolvedItem.downgraded` / `.reason` exist in memory only; `change_items` has no column for them, so a caught fabrication and an honest "we are unsure" look identical once stored |
| **How to close** | Anchor each generated section to the brief requirement it satisfies; add `downgrade_reason` to `change_items`; show the quote inline in the review UI |
| **Severity** | High for the marketing sentence, none for the demo — the mechanism is real, the wording must be narrower |

### A-15 — A client approval can be attributed to the client

| | |
|---|---|
| **Assumption** | `POST /agreements/{id}/approve` with `{"role": "client"}` represents the client's decision |
| **Status** | **Closed.** Magic-link invitations issue a session for the invited address, and `approve` binds a role to a principal: the owner may sign only as freelancer, and only an account **named on this project's invitation** may sign as client. Acceptance mints a session for a client who has no account; it is not what authorises a signature, because a client who already has an account can never accept the link (see A-17). Anything else is `409 ROLE_NOT_YOURS`, and the audit payload now says `role_source: session` rather than `self_asserted`. Proved by `test_two_distinct_principals_sign_and_the_agreement_is_approved` and `test_the_owner_cannot_sign_as_the_client` |
| **If false** | The timeline shows two approvals from one account. A judge who notices concludes the dual-approval claim is theatre — which would be correct |
| **How to close** | Magic-link exchange (block 1.5, unbuilt) plus a distinct actor id per approval, and `approve` refusing a second role from the same actor |
| **Severity** | High for the claim, low for the demo path — the payment path only needs the version to reach `APPROVED` |
| **Recorded in code** | `tests/test_invitations.py::test_two_distinct_principals_sign_and_the_agreement_is_approved` proves the closure, and `test_the_owner_cannot_sign_as_the_client` proves the hole is gone rather than documented |

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

### A-17 — The invitation link is returned to the inviter

| | |
|---|---|
| **Assumption** | Returning `invite_url` in the API response stands in for sending an email |
| **Status** | **True for the demo.** No mail is sent, so the token comes back in the response and the person who sent the invitation can read it. Safe only because `accept` refuses any address that already has an account (`409 EMAIL_ALREADY_REGISTERED`): a link can create a new principal, never assume an existing one. Before that refusal it was account takeover - the audit found it, and `tests/test_access_hardening.py::test_a_link_cannot_sign_in_an_account_that_already_exists` proves the closure |
| **If the refusal is ever removed** | The link becomes a password |
| **How to close** | Send the link by email and stop returning it; one `send_email` call and a test that the field is absent |
| **Severity** | Low now, critical if the refusal goes |
