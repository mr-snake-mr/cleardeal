# AI Architecture

**One rule above all others: the model proposes, the service disposes.** No model output reaches the database, the UI, or a payment decision until a deterministic validator has checked it against stored data.

The product's innovation is not "an LLM reads your contract". It is that **every claim the model makes must resolve to a row that already exists**. That is a software property, not a prompt property — which is why it survives a model swap.

## 1. The five modules

| # | Module key | Called from | Model role | Deterministic validator |
|---|---|---|---|---|
| M1 | `brief_analysis` | F-2 Analyse | Extract requirements, ambiguities, risks, assumptions | Every item must carry `source: "explicit" \| "inferred"`; explicit items must quote the brief |
| M2 | `agreement_draft` | F-3 Generate | Propose sections + milestones | `sum(milestones) == budget` exactly; every milestone has ≥1 deliverable and ≥1 acceptance check |
| M3 | `scope_classification` | F-6 Scope check | Classify each feature against the approved version | Each citation must resolve to `agreement_sections.id` **and** its `quote` must be a substring of that section's `body` |
| M4 | `impact_estimate` | F-6 Scope check | Effort range, cost, timeline for out-of-scope items | `low <= high`; cost derived from effort × the project's stored rate, not invented; `ALREADY_INCLUDED` items get `null` cost |
| M5 | `change_wording` | F-7 Change proposal | Rewrite the analysis as client-facing prose | No new facts: every sentence references a stored classification or a computed number; no number appears that is not in the payload |

M5 is deliberately last and deliberately narrow. It is the only module allowed to sound human, and it is allowed to sound human about **nothing it has not been given**.

## 2. Why retrieval precedes classification

M3 does not paste the whole agreement into the prompt and ask "is this in scope?".

```
1. Change request text
2. -> build candidate set: every agreement_sections row of the base version,
     scored by term overlap with the request (deterministic, no model)
3. -> send ONLY the top-k candidate sections (default k = 8) + the request
4. -> model returns classification + section_key + a verbatim quote
5. -> service resolves section_key -> agreement_sections.id
6. -> service asserts quote in section.body
```

Three consequences, all good:

- **Cheaper.** Eight sections is ~1.5k tokens; a 40-section agreement is ~12k.
- **Harder to hallucinate.** The model can only cite what it was shown. It cannot invent a section it never saw.
- **The citation is falsifiable.** Step 5 gives a foreign key; step 6 gives a string containment check. A wrong answer becomes a *detectable* wrong answer.

If step 5 or 6 fails, the item is **downgraded to `NEEDS_CLARIFICATION`** — never silently accepted, never repaired by asking the model again to "try harder". A downgrade is a correct outcome, not an error.

## 3. Output schemas

Structured output via Gemini's `responseSchema` (JSON Schema subset), then re-validated with Pydantic. Both layers are kept because they fail differently: the schema constrains generation, Pydantic catches semantic violations (positive amounts, ordering, cross-field rules).

### 3.1 M3 — the important one

```python
class Citation(BaseModel):
    section_key: str          # e.g. "AUTH"
    quote: str                # verbatim substring of that section's body

class ScopeItem(BaseModel):
    feature: str
    classification: Literal["ALREADY_INCLUDED", "OUT_OF_SCOPE", "NEEDS_CLARIFICATION"]
    explanation: str
    confidence: Literal["high", "medium", "low"]
    evidence: list[Citation]
```

Note what is **absent**: a numeric confidence. `high | medium | low` is a judgement the model can make honestly. `0.91` is a number it cannot. The schema makes the dishonest option unavailable rather than instructing against it.

### 3.2 What the schema layer silently drops

Verified against [Gemini structured output docs](https://ai.google.dev/gemini-api/docs/structured-output): `minItems` / `maxItems` are enforced, but `minLength`, `maxLength`, `minProperties`, `maxProperties`, `uniqueItems` and `contains` are **stripped without error**. Complex schemas can also return `400 INVALID_ARGUMENT`.

Therefore:

- Keep the generation schema shallow: no `$ref` chains, no deeply nested unions.
- Enforce every length/uniqueness rule **in Pydantic and the service**, never in the JSON Schema. A rule that lives only in the schema is a rule that does not exist.
- Test the schema against the live API once per model change, not just against Pydantic.

## 4. Prompt contracts

Prompts are files under `backend/app/ai/prompts/*.md`, versioned in git, hashed into `ai_runs.prompt_hash`. Not inline strings — an inline prompt cannot be diffed when behaviour changes.

| Module | System-prompt invariants |
|---|---|
| M1 | Never invent a requirement. `inferred` is valid and expected. If the brief does not state a deadline, emit an assumption, not a date. |
| M2 | Milestone amounts are integers in USD cents. Their sum must equal the stated budget. Do not round to make it fit; if it cannot fit, say so. |
| M3 | Cite only from the provided sections. Use `section_key` exactly as given. Quote verbatim, 10–300 characters. If no section addresses the feature, return `NEEDS_CLARIFICATION` with empty evidence. |
| M4 | Effort in whole hours. Cost = hours × rate. Return a range with `low <= high`. Do not estimate a feature you were not asked about. |
| M5 | Use only the supplied classifications and numbers. No adjectives that imply legal advice. No promises about future work. |

Every prompt carries the same closing line: *"If the input does not support a confident answer, return the uncertain classification. A downgrade is a correct answer; a guess is not."*

## 5. Validation pipeline (per call)

```
build prompt -> call model -> JSON parse -> Pydantic parse -> semantic checks
   -> evidence resolution (M3 only) -> persist ai_run -> return
       any failure at any step: no partial write, typed error, retry offered
```

| Step | Failure | User sees | Database |
|---|---|---|---|
| HTTP | timeout / 5xx | one retry, then `503 AI_UNAVAILABLE` | `"transport_error"` |
| HTTP | 4xx (bad key or schema) | no retry; `502 AI_INVALID_REQUEST` — an outage is not what happened | `"client_error"` |
| config | no API key | `503 AI_UNAVAILABLE` before any spend | `"not_configured"` |
| success | — | the validated object | `"ok"` |
| JSON parse | malformed | no retry, then `502 AI_INVALID_OUTPUT` — the same text parses the same way twice | `"parse_error"` |
| Pydantic | wrong shape | same | `"schema_error"` |
| Semantic | invariant broken | same | `"semantic_error"` |
| Evidence | unresolvable | item downgraded, **run succeeds** | `"downgraded"` |

Exactly one retry, then fail. An unbounded retry loop against a flaky provider is how a demo dies at 2:40.

## 6. The `ai_runs` record

One row per call: `module`, `model`, `prompt_hash`, `input_tokens`, `output_tokens`, `latency_ms`, `validation_status`.

This exists for a judging reason, not an ops reason: a judge can open the database and see that the AI calls were real, when they happened, and whether their output was accepted. A recorded run is evidence. A claim that "we use AI" is not.

## 7. Cost model

Verified pricing ([Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing), 2026-11-09): Gemini 3.8 Flash **\$0.75 / 1M input, \$3.75 / 1M output**, free tier available; 4 Argon at \$2 / \$10.

| Module | In | Out | Calls per demo | Cost per demo |
|---|---:|---:|---:|---:|
| M1 | ~1.2k | ~0.8k | 1 | \$0.0039 |
| M2 | ~2.0k | ~1.5k | 1 | \$0.0071 |
| M3 | ~1.6k | ~0.5k | 1 | \$0.0031 |
| M4 | ~0.8k | ~0.3k | 1 | \$0.0017 |
| M5 | ~0.6k | ~0.4k | 1 | \$0.0020 |

Under \$0.02 for a full run. Cost is not a constraint; latency is. Budget 3s per call, 10s for a full scope check.

## 8. What the AI is not allowed to do

| Forbidden | Enforced by |
|---|---|
| Move money, or decide a payment status | Payments service reads only PayPal responses; the AI has no write path to `payments` |
| Change a budget | M2 output is checked against `projects.budget_amount`; mismatch blocks the send |
| Approve anything | Approval is a human action with an authenticated user id |
| Rewrite an approved version | Versions are append-only; a change creates `v(n+1)` |
| Cite a section that does not exist | Evidence resolution against `agreement_sections` |
| Return a fake confidence score | Schema restricts to `high/medium/low` |

## 9. Failure of the provider during the demo

The demo path must survive a provider outage without lying. `AI_UNAVAILABLE` is surfaced with a retry button; the project stays in its prior state; the payment flow is unaffected. If the outage is prolonged, the fallback is a **recorded-run replay**: the exact stored `ai_runs` payload for a fixture project, mounted behind a clearly-labelled `DEMO_REPLAY` flag that the UI displays on screen.

That flag is never enabled during judging unless the provider is genuinely down, and when it is on, the screen says so. A silent replay would be the one unrecoverable mistake — it turns every other honest artefact into something a judge cannot trust.

## 10. Model choice

| Candidate | Decision | Reason |
|---|---|---|
| Gemini 3.8 Flash | **default** | Free tier suits a 3-day build; structured output; cost negligible |
| 4 Argon | escalation only | 2.7× cost, no capability this task needs |
| Local model | rejected | Cannot guarantee structured output under time pressure |

`model` is a column in `ai_runs`, not a constant in code, so a swap is visible in the record rather than invisible in a diff.
