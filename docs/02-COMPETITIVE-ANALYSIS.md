# Competitive and Differentiation Analysis

## 1. How to read this

Two source classes, labelled throughout:

- **Verified** — official documentation, checked directly.
- **Secondary** — third-party comparison articles. Directional only; re-check at source before any claim is spoken in a pitch.

I have **not** verified any competitor's feature list against their own documentation. Nothing below asserts that a competitor lacks a capability.

## 2. Four layers

| Layer | Who | Sells | Why it does not close the gap |
|---|---|---|---|
| Contract & client ops | HoneyBook, Bonsai, Dubsado | Proposals, contracts, e-signature, invoices, client portal, payments | Whether the agreement is stored as structured, versioned data versus a signed artefact is **unverified** (A-14). That is the question this product tests. |
| Marketplaces | Upwork, Fiverr, Contra, Toptal | Discovery, escrow, disputes | Work is bought inside the platform; the freelancer's own clients sit outside it |
| Payments | PayPal, Stripe, Wise | Movement of money | A capture is a number, not a milestone |
| AI contract tools | Various drafters | Draft or summarise an agreement | Generation is the easy half; comparison against a fixed reference is the hard half |

**Secondary, needs verifying:** HoneyBook is described in third-party comparisons as bundling proposals, contracts, invoices, payments and a client portal on every plan; Bonsai as the simpler contracts-and-invoices option; Dubsado as the more configurable forms-and-workflows option. Directional only.

## 3. What is genuinely already solved

Say this plainly — judges reward precision, not bravado:

- Collecting a signature: solved, cheaply, by several products.
- Splitting a project into milestones: solved in every template.
- Taking a card or PayPal payment: solved, trivially.
- Sending an invoice: solved.
- Generating contract prose with an LLM: solved, and no longer interesting alone.

## 4. The gap we address

**Whether an incumbent maintains the approved scope as a queryable, versioned object and answers "is this new request inside it?" with cited evidence, then prices the difference, then issues a new version — while leaving captured payments immutable. I have not verified that any of them does; the sentence describes the gap we are testing, not a fact about them.**

Three properties make it defensible rather than a sprint-sized feature:

1. **The agreement is data, not a document.** Sections have ids; milestones reconcile to a budget; evidence cites by id and is validated.
2. **Comparison is against a specific approved version.** Version identity *is* the product.
3. **The comparison output is an artefact with a lifecycle** — proposal, edit, approval, new version — not a chat reply that evaporates.

A tool that stored the agreement only as prose has no way to do (2) or (3). Whether an incumbent already does is unverified (A-14).

## 5. Positioning

> HoneyBook and Bonsai help you sign a contract. ClearDeal helps you keep it true.
>
> When a client asks for one more thing, ClearDeal quotes the clause that excludes it, prices the change, and turns it into an approved version two — without touching a payment you already received.

## 6. What we must not say

| Claim | Why |
|---|---|
| "Nobody else does contracts plus payments" | HoneyBook, Bonsai, Dubsado all do both |
| "We invented milestone payments" | False |
| "We hold funds in escrow" | False unless implemented and licensed. We do not |
| "Freelancers get paid automatically" | False in v1 |
| "X% less scope creep" | No study exists |
| "Trusted by N freelancers" | Zero users |

## 7. Validation still owed

1. Interview 3–5 freelancers. One question only: *"Tell me about the last time a client asked for something you thought was out of scope. What did you do?"* Record verbatim. Do not lead.
2. Ask 2 clients: *"When you paid a milestone, could you tell exactly what you were paying for?"*
3. Put the best quotes in the README under **User research**, with sample size.

If interviews do not happen, the README says *"hypothesis, not validated"* and the demo claims nothing.
