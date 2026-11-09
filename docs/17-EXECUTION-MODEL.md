# Execution Model

Two roles that are never merged, and a skill gate that applies to every write.

This document exists because the failure mode of an AI-built submission is not bad code. It is **unverified claims**: work that was described as done, audited by the author, and believed.

## 1. The two roles

| | Author | Auditor |
|---|---|---|
| Writes files | Yes | **No** |
| Runs tests | Yes | Yes (read-only re-run) |
| Reads files | Yes | Yes |
| May return PASS | **No** | **Yes, only** |
| Context | Full task history | **Fresh** — no knowledge of the author's reasoning |
| Count | 1 | ≥ 1, never the author |

The auditor is spawned with a read-only toolset (`explore`: glob, grep, read, list) and given the task record plus the original requirement. It is shown what was claimed; it is not shown what the author was thinking.

**Why a fresh context matters.** An author who has just written 400 lines will read those lines and see the intent, not the code. A reader who has never seen them sees the code. Convergence evidence: the first audit of this dossier returned FAIL with **14 substantiated findings**, two of them critical contradictions between documents. The author's own review had found none of them.

**Why the built-in `verification` agent is not used for document audits.** It has a fixed artefact budget and refuses directory reads — it returned PARTIAL without opening a single file. `explore` is the correct auditor for documents and code; `general` only when the audit must execute commands.

## 2. The gate record

No task passes without this record, and the record carries **real output**, not a summary of it.

```
TASK      what was built (id, one line)
SKILLS    applied set + ruled-out set with reasons
CHANGES   files touched, each with a one-line why
EVIDENCE  verbatim command output — test counts, coverage, exit codes
AUDIT     SPEC: PASS | FAIL <findings>
          QUALITY: PASS | FAIL <findings>
          VERDICT: PASS | FAIL
```

`EVIDENCE` is the field that does the work. "All tests pass" is not evidence. `174 passed, 95.58% coverage, exit 0` is.

The auditor checks three things, in order:

1. **Does the evidence show what was claimed?** Re-run one command. A claim of 174 tests is checked by counting.
2. **Does the code do what the task said?** Read the implementation, not the diff summary.
3. **Would the wrong implementation also pass?** This is the question that produces useful findings. It is how `test_audit_events_survive_project_deletion` was found to mask the behaviour it tested.

## 3. Author skill mandate — minimum sets per activity

Before every step, the author sweeps the skill catalog, reads each applicable `SKILL.md`, adopts it as the procedure, and declares both the applied and the ruled-out set. Declaring is not optional: an undeclared skill is an unused skill.

| Activity | Mandatory | Optional | Usually ruled out |
|---|---|---|---|
| Any task start | `using-superpowers` (or `using-agent-skills`) | — | — |
| Writing or editing code | `karpathy-guidelines`, `ponytail` | `python-patterns` / `typescript-patterns`, `fastapi-patterns`, `react-patterns` | framework playbooks for frameworks not in use |
| Implementing a feature | `test-driven-development`, `incremental-implementation` | `spec-driven-development` | — |
| Database or schema work | `database-migrations` | `postgres-patterns`, `sqlalchemy` guidance | NoSQL playbooks |
| Payment or money code | `security-and-hardening`, `error-handling` | `fastapi-patterns` | — |
| Auth or credentials | `security-and-hardening` | `api-and-interface-design` | — |
| AI/LLM integration | `cost-aware-llm-pipeline`, `context-engineering` | `prompt-optimizer` | — |
| Frontend | `frontend-ui-engineering`, `accessibility` | `react-patterns`, `design-system`, `motion-*` | — |
| Writing a document | `writing-plans` (structure), `documentation-and-adrs` | `spec-driven-development` | — |
| Debugging | `systematic-debugging` | `debugging-and-error-recovery` | — |
| Before claiming completion | `verification-before-completion`, `verification-loop`, `agent-self-evaluation` | — | — |
| Reviewing a change | `ponytail-review`, `requesting-code-review` | `code-review-and-quality` | — |
| Any commit or branch | `git-workflow-and-versioning` | `conventional-commit-review` | — |

`ponytail` and `karpathy-guidelines` are constant constraints, not per-activity choices: the smallest change that works, and no speculative abstraction.

### The rationalisations that are rejected

`using-superpowers` names them, and they are named here because they were all tempting during this build:

- "It's a one-line change." — One-line changes caused two of the fourteen findings.
- "The skill is overkill here." — The judgement that a skill is overkill is made by the process the skill describes.
- "I already know this." — Knowing it and doing it are different, and the audit only sees the doing.
- "Let me just check the file first." — The file is checked *inside* the skill, not before it.
- "The tests pass, so it's done." — Tests that cannot fail prove nothing; see 12 §5.

## 4. The fix loop

```
Round 1  author fixes → scoped re-review of the fix diff only
Round 2  author fixes → scoped re-review
Round 3  author fixes → scoped re-review
Round 4  escalate to a stronger model / more capable agent
At cap   park the finding with a written Ruling: what it is, why it is
         not being fixed now, what would trigger a fix
```

Three rules:

1. **Scoped re-review.** Round *n* audits the diff from round *n−1*, not the whole artefact. A full re-audit every round burns the budget on already-cleared ground.
2. **A fix that creates a new finding is normal.** Round 2 of this dossier found **6 new problems created by round 1's fixes** — one document said money is `Decimal`, another said INTEGER cents; one said `422` for an amount mismatch, another said `RECONCILIATION_REQUIRED`. That is what a fix loop is for.
3. **Never silently drop.** A finding not fixed is written down. A parked finding is a documented decision; a forgotten finding is a defect.

## 5. Stop conditions

The author stops and reports rather than continuing when:

| Condition | Why |
|---|---|
| The same command fails twice with the same error | A third identical attempt is not a fix |
| Evidence contradicts a claim already made | The claim must be corrected before more work rests on it |
| An assumption in doc 16 turns out false | Documents and UI must change, not the wording of a claim |
| A write cannot be verified | An unverified write is an unknown state |
| A destructive action is needed (delete, force-push, drop) | Ask. Always. |
| Data appears to have been lost | Report the loss and its evidence. Never reconstruct from memory and present it as the original. |

## 6. Version control — non-negotiable

`git init` runs **before the first file is written**, and a commit lands at every task boundary.

This rule is in this document because it was learned the hard way: on 2026-11-09 the working folder was deleted by an unidentified process and, with no repository, **nothing was recoverable** (14, R-11). A copy that exists in one place is not a copy.

| Rule | Enforcement |
|---|---|
| `git init` before the first file | First action of any new project |
| Commit at every task boundary | The gate record includes `git log` |
| A task cannot PASS with an empty working tree history | Auditor checks |
| Never `--force` push, never rewrite published history | Stop condition |

## 7. What this model costs, and why it is still worth it

It is slower. Roughly a third of the time goes to gates, records and re-reviews that produce no visible feature.

It is also the only reason this dossier can be trusted: 14 findings, then 6, then PASS. Every one of those 20 findings was a real defect in a document that had already been read carefully by its author. The loop is not bureaucracy; it is the difference between "we wrote 17 documents" and "we have 17 documents that survive a hostile reader".
