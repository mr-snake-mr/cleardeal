# Edge-Case Checklist

Every row is a behaviour the build must produce, and the test that proves it. A row without a test is a wish.

Legend — **T** = the test that proves the row. **E** = the Playwright scenario in [12](12-TESTING-STRATEGY.md) §4, `E1`–`E9`.

**Read this before trusting the T column.** At the 09-NOV rebuild, six of the named tests exist:

`test_budget_must_be_positive` (I-1, I-2) · `test_only_one_active_payment_per_milestone` (P-2) · `test_money_round_trips_without_drift` (I-4) · `test_money_records_block_project_deletion` (D-1) · `test_audit_events_survive_project_deletion` (D-3) · `test_foreign_key_enforcement_is_actually_on` (D-7).

Every other id is **the name the test will have when its block in [13](13-SCHEDULE.md) runs**. That is a specification, not evidence. Presenting a named-but-unwritten test as proof is the exact failure this dossier exists to prevent, so it is stated here rather than discovered by a reader.

## 1. Input and validation

| # | Case | Required behaviour | Proof |
|---|---|---|---|
| I-1 | Budget `0` | `422`, no project row | T-`test_budget_must_be_positive` |
| I-2 | Budget `-5` | `422`, no project row | T-`test_budget_must_be_positive` |
| I-3 | Budget `"300.001"` | `422` — three decimals is not money | T-`test_budget_rejects_sub_cent` |
| I-4 | Budget as JSON number `300.1` | accepted, stored as exactly `300.10`, never `300.09999` | T-`test_money_round_trips_without_drift` |
| I-5 | Brief of 1 character | `422` — minimum 20 chars; a one-word brief cannot be analysed | T-`test_brief_minimum_length` |
| I-6 | Brief of 100 000 chars | `422` with a clear limit, not a 500 and not a truncated analysis | T-`test_brief_maximum_length` |
| I-7 | Email `"not an email"` | `422` | T-`test_invalid_email_rejected` |
| I-8 | Deadline in the past | `422` — a project cannot be due yesterday | T-`test_deadline_must_be_future` |
| I-9 | Unknown field `amount` on payment creation | `422` (`extra="forbid"`), not silently ignored | E9 |
| I-10 | Unicode brief (emoji, RTL, CJK) | stored and rendered without mojibake | T-`test_unicode_brief_round_trip` |
| I-11 | Brief containing `<script>alert(1)</script>` | rendered as text, never executed | manual sweep (12 §8) |
| I-12 | Duplicate project name for the same owner | allowed — names are not identity | T-`test_duplicate_project_name_allowed` |

## 2. AI

| # | Case | Required behaviour | Proof |
|---|---|---|---|
| AI-1 | Provider returns valid JSON with a bad enum | item rejected, `502 AI_INVALID_OUTPUT`, nothing persisted | T-`test_schema_violation_rejected` |
| AI-2 | Provider returns prose around the JSON | parse fails, one retry, then `502`; no partial write | T-`test_prose_wrapped_output_rejected` |
| AI-3 | Provider times out | `503 AI_UNAVAILABLE`, project stays `DRAFT`, retry offered | T-`test_ai_timeout_leaves_no_state` |
| AI-4 | Provider returns 500 twice | one retry then `503`; exactly two calls, no loop | T-`test_single_retry_then_fail` |
| AI-5 | Milestone sum ≠ budget from M2 | generation rejected, delta returned, no version created | T-`test_milestone_sum_enforced_on_generate` |
| AI-6 | M3 cites a `section_key` that does not exist | item downgraded to `NEEDS_CLARIFICATION` | T-`test_unknown_section_key_downgrades` |
| AI-7 | M3 cites a real key with a quote **not** in the body | downgraded; the fabricated quote is discarded | T-`test_quote_not_in_body_downgrades` |
| AI-8 | M3 cites a real key, quote differs only in whitespace/newlines | normalised before comparison, **accepted** — a false downgrade is also a bug | T-`test_quote_whitespace_normalised` |
| AI-9 | M3 returns zero items for a non-empty request | `502 AI_INVALID_OUTPUT` — an empty answer to a real question is a failure | T-`test_empty_classification_rejected` |
| AI-10 | Mixed request: one included, one out of scope, one unclear | three items, each classified independently | E5 |
| AI-11 | `ALREADY_INCLUDED` item with a cost | cost forced to `null` server-side | T-`test_included_item_cost_nulled` |
| AI-12 | Model invents a confidence of `0.91` | schema rejects; only `high/medium/low` accepted | T-`test_numeric_confidence_rejected` |
| AI-13 | Prompt-injection text inside a change request | classification still resolves or downgrades; never approves | E5 |
| AI-14 | Two concurrent M3 calls on one change request | one wins the state transition, the other `409` | T-`test_concurrent_analysis_conflict` |
| AI-15 | `ai_runs` row written for every call, including failures | count matches provider call count | T-`test_every_call_logged` |

## 3. Agreement and versions

| # | Case | Required behaviour | Proof |
|---|---|---|---|
| G-1 | Send with sum ≠ budget | `409 MILESTONE_SUM_MISMATCH`, status unchanged | T-`test_send_blocked_on_mismatch` |
| G-2 | Send with zero milestones | `422` | T-`test_send_requires_milestone` |
| G-3 | Patch an `APPROVED` version | `409`, zero mutation | T-`test_approved_version_is_immutable` |
| G-4 | Approve v1 when v2 exists | `409 STALE_VERSION` | E8 |
| G-5 | Approve twice with the same user | second is `409`, `client_approved_at` not overwritten | T-`test_double_approval_rejected` |
| G-6 | Freelancer approves, client approves | `APPROVED`, both timestamps set, `content_hash` stable | T-`test_dual_approval_sets_both_stamps` |
| G-7 | Sections edited between approvals | rejected — the hash covers sections, so a mid-flight edit is a state error, not a silent divergence | T-`test_edit_between_approvals_rejected` |
| G-8 | Change approved | `v(n+1)` created, `v(n)` → `SUPERSEDED`, old rows untouched | T-`test_change_approval_creates_new_version` |
| G-9 | Change approved on a project with a captured payment | payment row byte-identical afterwards | T-`test_captured_payment_untouched_by_new_version` |
| G-10 | Two `section_key` collisions in one version | DB unique constraint raises, request `409` | T-`test_duplicate_section_key_rejected` |

## 4. Payments

| # | Case | Required behaviour | Proof |
|---|---|---|---|
| P-1 | Payment created twice with the same `Idempotency-Key` | one row, one order, second is `200` | T-`test_idempotent_payment_creation` |
| P-2 | Two live payments for one milestone | `409 PAYMENT_ALREADY_ACTIVE` (partial unique index) | T-`test_only_one_active_payment_per_milestone` |
| P-3 | Milestone already `PAID` | `409` — no second payment | T-`test_paid_milestone_cannot_be_recharged` |
| P-4 | Capture on a `CAPTURED` payment | `409 PAYMENT_STATE_INVALID` | T-`test_double_capture_rejected` |
| P-5 | Capture returns `COMPLETED` but amount differs | `409 RECONCILIATION_REQUIRED`, `captured_at` stays null | T-`test_amount_mismatch_blocks_capture` |
| P-6 | Capture returns `COMPLETED` but payee differs from the bound merchant | `RECONCILIATION_REQUIRED`, paid UI blocked | T-`test_payee_mismatch_blocks_capture` |
| P-7 | Capture network timeout | state fetched with `GET /orders/{id}` before any retry; **never** a second capture POST | T-`test_capture_timeout_reads_before_retry` |
| P-8 | Capture succeeds, DB write fails | payment converges from the webhook; no duplicate row | T-`test_capture_db_failure_converges_from_webhook` |
| P-9 | No connected merchant, Model B | `409 MERCHANT_NOT_CONNECTED`, no order attempted | T-`test_payment_requires_connected_merchant` |
| P-10 | Merchant credentials revoked at PayPal | `400 INVALID_CREDENTIALS`, merchant row → `INVALID`, clear UI | T-`test_invalid_merchant_credentials_marked` |
| P-11 | `paypal_order_id` tampered in the capture request | `409`, nothing captured | E9 |
| P-12 | Amount `0.01` | allowed and exact — a one-cent invoice is valid money | T-`test_one_cent_payment_exact` |

## 5. Webhooks

| # | Case | Required behaviour | Proof |
|---|---|---|---|
| W-1 | Same `event_id` delivered twice | `200`, zero new rows | T-`test_duplicate_webhook_idempotent` |
| W-2 | Body altered by one byte | `400`, zero state change | T-`test_tampered_body_rejected` |
| W-3 | Signature header missing | `400` | T-`test_missing_signature_rejected` |
| W-4 | `paypal-cert-url` points at an attacker host | `400`, no outbound request | T-`test_cert_url_allowlisted` |
| W-5 | Webhook arrives before the capture response | both converge on the capture id; exactly one row reaches `CAPTURED` | T-`test_webhook_before_capture_converges` |
| W-6 | Event for an unknown capture id | `200`, logged, no row created — PayPal must not retry forever over our missing record | T-`test_unknown_capture_logged_only` |
| W-7 | Handler raises mid-processing | error stored in `process_error`, `200` returned, retry safe | T-`test_handler_error_recorded` |
| W-8 | Out-of-order `DENIED` then `COMPLETED` | last verified state wins, both events stored | T-`test_out_of_order_events_applied_in_order` |

## 6. Concurrency and time

| # | Case | Required behaviour | Proof |
|---|---|---|---|
| C-1 | Two browsers click "pay" simultaneously | one payment row, one order | T-`test_concurrent_payment_creation` |
| C-2 | Two clients approve the same version concurrently | one succeeds, one `409` | T-`test_concurrent_approval` |
| C-3 | Two scope checks on the same project | independent rows, no shared mutable state | T-`test_parallel_changes_independent` |
| C-4 | Milestone `due_date` is today | allowed — due today is not overdue | T-`test_due_today_allowed` |
| C-5 | Timestamps stored UTC, displayed local | one conversion point (`lib/format.ts`), no naive datetimes in the DB | T-`test_timestamps_are_utc` |
| C-6 | Two audit events in the same second | stable order via `created_at, id` | T-`test_timeline_order_stable` |

## 7. Data and deletion

| # | Case | Required behaviour | Proof |
|---|---|---|---|
| D-1 | Delete a project with payments | refused (`ON DELETE RESTRICT`), `409` | T-`test_money_records_block_project_deletion` |
| D-2 | Delete a project without payments | cascades agreements, versions, sections, milestones, changes | T-`test_project_cascade` |
| D-3 | Delete a project with audit rows | audit rows survive with `project_id = NULL` | T-`test_audit_events_survive_project_deletion` |
| D-4 | Delete an `ai_runs` project | run rows survive with `project_id = NULL` | T-`test_ai_runs_survive_project_deletion` |
| D-5 | Fresh database, no migrations applied | health reports `migrations: "pending"` with HTTP `503`, and the app still boots | T-`test_health_reports_503_when_migrations_are_pending` |
| D-6 | Database file deleted underneath a running process | health reports `error`, not "ok" and not "pending" | T-`test_health_reports_degraded_when_db_ping_fails` |
| D-7 | Foreign key enforced on a fresh connection | pragma actually on; a violating insert raises | T-`test_foreign_key_enforcement_is_actually_on` |

## 8. Auth and authorisation

| # | Case | Required behaviour | Proof |
|---|---|---|---|
| S-1 | Valid credentials, wrong case email | normalised, login succeeds | T-`test_email_case_insensitive` |
| S-2 | Six wrong passwords | `401` each; rate limit engages at 10/min | T-`test_login_rate_limited` |
| S-3 | Expired magic link | `410`, clear message, no session | T-`test_expired_invitation_rejected` |
| S-4 | Magic link used twice | second use `410` | T-`test_invitation_single_use` |
| S-5 | Review session calls a workspace endpoint | `401` | manual sweep (12 §8) |
| S-6 | User B reads user A's project | `404`, not `403` | manual sweep (12 §8) |
| S-7 | No session at all | `401` on every non-public route | T-`test_unauthenticated_rejected` |

## 9. UI states

| # | Case | Required behaviour |
|---|---|---|
| U-1 | Slow network (3s latency) | skeletons in final layout, no spinner collapse, no layout shift |
| U-2 | Project with no agreement yet | empty state naming the one action that creates one |
| U-3 | AI error | inline error with `code`, `request_id`, Retry; the page still works |
| U-4 | `RECONCILIATION_REQUIRED` | amber hold panel, capture id shown, no "paid" language |
| U-5 | `payee_merchant_id` null (A-2 unconfirmed) | payee row simply not rendered; no claim about the recipient |
| U-6 | 320px viewport | no horizontal scroll on the scope card |
| U-7 | Keyboard only | every control reachable, focus visible, dialog traps and restores focus |
| U-8 | `prefers-reduced-motion` | transitions removed, nothing hidden |
| U-9 | 200% browser zoom | no clipped text, no overlapping badges |

## 10. Operational

| # | Case | Required behaviour |
|---|---|---|
| O-1 | Cold start with an empty database | migrations run, app serves, health `ok` with a revision |
| O-2 | `PAYPAL_CLIENT_SECRET` missing at startup | fail fast with a named variable, not a 500 on first payment |
| O-3 | `CREDENTIALS_ENCRYPTION_KEY` missing with merchant rows present | refuse to start; never silently invalidate merchants |
| O-4 | Gemini key missing | app boots, AI endpoints `503 AI_UNAVAILABLE`, payment flow unaffected |
| O-5 | Webhook received on a deploy without migrations applied | nothing written; the retry after deploy succeeds |

## 11. The three cases most likely to be skipped

Called out because they are the ones that pass review and fail a judge:

1. **P-7** — capture timeout. The instinct is `try/except: retry`. That instinct double-captures.
2. **A-8** — a *false* downgrade from whitespace normalisation. Only testing the invented-quote direction leaves the opposite bug invisible.
3. **D-3** — audit survives project deletion. An earlier version of this test performed the `SET NULL` by hand, which meant `CASCADE` would have passed too. The database must perform the deletion for the test to mean anything.
