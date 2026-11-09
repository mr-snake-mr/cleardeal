# MVP Scope and Cut Line

The MVP is not "the product, smaller". It is the smallest build in which the central claim — **an approved scope, checked against, with evidence that resolves** — is demonstrably true, with a real payment attached to it.

## 1. In

| Capability | Why it is in | Doc |
|---|---|---|
| Project from a brief, USD budget only | Entry point; the brief is the raw material | 01 F-1 |
| AI requirement extraction, `explicit` vs `inferred` | Without the distinction the output is indistinguishable from a guess | 05 M1 |
| Agreement with sections, milestones summing exactly to budget | The invariant everything else rests on | 07 §3 |
| Immutable versions, dual approval, `content_hash` | "What we agreed" only means something if it cannot change underneath | 07 §3, 08 §4 |
| PayPal Sandbox order + server-side capture + verification | The PayPal half of the hackathon, done honestly | 04 §4 |
| Verified webhook, idempotent | Proves the integration is real, not a screenshot | 04 §4.3 |
| Scope check with **resolved** evidence | The innovation | 05 M3 |
| Change proposal → approval → `v(n+1)` | Closes the loop: the answer becomes an artefact | 01 F-7 |
| Audit timeline | The proof surface a judge can scroll | 01 F-8 |
| Magic-link client review | The second persona without a second auth system | 08 §2 |

## 2. Out

| Excluded | Reason |
|---|---|
| Payouts, escrow, platform fee, marketplace | Requires PayPal partner approval; claiming it would be false (04 §1.3) |
| Real money, KYC, tax, invoicing | Legal surface far beyond three days |
| Multicurrency | `CHECK (currency IN ('USD'))` — one currency, exactly right, beats five approximately right |
| Chat, task boards, time tracking, file storage | Adjacent products; each is its own hackathon |
| Mobile apps | A responsive web app is the same product with one codebase |
| Refunds UI | The webhook handles `REFUNDED`; the button is not the demo |
| Notifications, digests, emails beyond invitations | Email is a stub (A-9) |
| Team accounts, roles, permissions beyond owner/client | One freelancer, one client |

Every exclusion is stated in the README. A judge who finds an unstated gap trusts nothing else; a judge who reads a stated one credits the judgement.

## 3. The cut line, in order

If time runs out, cut **down this list** — top first. This ordering is the whole document.

| # | Cut | Cost of cutting | What survives |
|---|---|---|---|
| 1 | M5 client-facing wording | Proposals read as structured findings, not prose | Everything; the card is unchanged in substance |
| 2 | Timeline page (show last 5 events inline instead) | Less scrollable proof | Audit data still written and visible |
| 3 | Impact ranges → a fixed estimate table | Less "AI", same numbers | Classification + evidence intact |
| 4 | Client review page becomes read-only | Approve happens in the demo via the freelancer's view | Version + hash still demonstrated |
| 5 | M1 brief analysis → a template with `explicit`/`inferred` tags | Less magic at the start | The scope check is untouched |
| 6 | Navigation, project list, settings pages | One linear page | The demo path still works |
| 7 | **Nothing further** | — | — |

**Never cut, in any circumstance:**

- Evidence resolution against `agreement_sections`
- `sum(milestones) == budget`
- Capture verification before `CAPTURED`
- Webhook signature verification
- Version immutability

Those five are the product. Cutting any of them leaves a demo that looks identical and proves nothing — which is the specific failure this dossier exists to avoid.

## 4. Minimum shippable, defined

If only half the schedule survives, this is the smallest build that is still worth submitting:

```
1 project → brief → agreement with 3 milestones → approve
→ pay milestone 1 in Sandbox → capture verified
→ one change request → one OUT_OF_SCOPE item with a real citation
→ one page, no navigation, no timeline, no client review
```

That is roughly 60% of the in-scope list and 100% of the claim. It is a submission, not an apology.

## 5. Judging coverage per capability

| Criterion | Carried by | If cut |
|---|---|---|
| Technology | Server-side order + verified capture + idempotent webhook | Cannot be cut; it is the entry |
| Design | Signature screen, two coherent surfaces | Cut 6 hurts this most |
| Impact | Persona + measured workaround | Independent of the build |
| Innovation | Evidence resolution | Cannot be cut |
| Presentation | 2:45 video, real transactions only | Independent of the build |

## 6. Future scope (documented, not built)

Ordered by what a real user would ask for next, not by what is fun to build:

1. **OAuth seller onboarding** — replaces stored credentials with PayPal's seller consent flow. This is the first thing a production version must do (R-4).
2. **Multiparty + partner fee** — once approved; the fee abstraction already exists as `MerchantCredentials`.
3. **Postgres + RLS** — closes R-9.
4. **Real email** — closes A-9.
5. **Agreement PDF + e-signature** — the artefact clients actually want to keep.
6. **Multi-currency** — requires the `Money` type to carry a currency, not just an amount.
7. **Dispute timeline export** — the log already exists; the export is a day's work.
8. **Mobile client view** — clients read on phones; the review page is the natural first app.

Items 1–3 are engineering debt with a named fix. Items 4–8 are product expansion. Mixing them in one list is how a roadmap becomes a wish list.

## 7. What "done" means for this submission

Doc 01 §9 lists the six success criteria. Restated as a binary:

- [ ] Nine E2E scenarios pass against real Sandbox and a real model call
- [ ] `sum(milestones) == budget` on every version, proven by test and by a CI query
- [ ] A replayed webhook creates zero new rows
- [ ] A tampered frontend amount is ignored
- [ ] Every AI classification in the demo resolves its evidence to a real section id
- [ ] No dead control on the demo path

Six checkboxes. Five are automated. The sixth is a two-minute manual sweep before recording. If any box is unchecked at submission time, the honest move is to say so in the README rather than to describe the intended behaviour — the dossier's credibility is the only asset that cannot be rebuilt in an hour.
