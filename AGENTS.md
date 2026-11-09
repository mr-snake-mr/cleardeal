# AGENTS.md — ClearDeal project rules

Scope: everything under `cleardeal/`. Binding on every agent, every task, every time.

---

## 1. Two roles, never merged

| Role | Who | May do | May not do |
|---|---|---|---|
| **Author** | The implementing agent | Write code, tests, docs, commits | Approve its own work; mark a task complete |
| **Auditor** | A separate agent, fresh context, read-only | Read everything, run checks, return a verdict | Edit files, fix findings, approve its own findings |

**A task is complete only when the Auditor returns `PASS`.** The Author's report is a claim, not evidence.

## 2. The author's skill mandate

Before any step — write, edit, delete, refactor, test, deploy — the Author must:

1. Sweep the workspace skill catalog (`hacko/SKILLS-CATALOG.md` — outside this repo, not committed).
2. Read each applicable `SKILL.md` in full.
3. Adopt it as the procedure for this step, following its checklists and stop conditions.
4. Declare the applied and ruled-out sets in the task brief.

Reading a skill then working outside it is a violation, not a partial pass. Rejected on sight: *"it's trivial"*, *"the skill is overkill"*, *"I already know this pattern"*, *"let me check the file first"*.

Per-activity minimum sets: [`docs/17-EXECUTION-MODEL.md`](docs/17-EXECUTION-MODEL.md) §3.

## 3. The gate

Every task, without exception:

```
TASK       <id> — <one line>
SKILLS     <applied>  |  ruled out: <skill -> reason>
CHANGES    <files>
EVIDENCE   <command> -> <output>
--- AUDIT ---
SPEC       PASS | FAIL  <what the task required>
QUALITY    PASS | FAIL  <findings by severity>
CANNOT     <items not verifiable from the diff>
VERDICT    PASS | PARTIAL | FAIL
```

The Auditor never returns `PASS` on a bare claim. No command output, no pass.

## 4. Fix loop

- Critical/Important findings → the **same** Author resumes, so intent survives.
- Each round ends with a scoped re-review of only the fix diff.
- Round cap: 3. At the cap, the orchestrator writes `Ruling: <decision> — <why> — <cost if wrong>`.
- Silent discard of a finding is forbidden. So is "the fix was small, skip re-review".

## 5. Stop conditions

The Author stops and asks — it does not rule — on exactly four things:

1. An irreversible or destructive operation.
2. A security-sensitive action (credentials, permissions, payment-flow change).
3. A side effect outside the worktree: push, deploy, publish.
4. A plan so broken that every path forward is a guess.

Everything else is a `Ruling:` and the work continues.

## 6. Version control — non-negotiable

`git init` happened before the first file. **Commit at every task boundary.** On 2026-11-09 the workspace was deleted by an unidentified process with no recovery path; the only reason this project still exists is that the loss was caught early.

- Never commit secrets, `.env`, `*.db`, or `.venv/`.
- Commit messages: `feat:`, `fix:`, `test:`, `docs:`, `chore:`.

## 7. Permanent coding rules

- SOLID, DRY, KISS. Small functions. Type safety. No speculative abstraction.
- Python: PEP 8, type hints, Pydantic at boundaries, business logic in `services/` never `routers/`.
- TypeScript: no `any`, explicit prop interfaces, all fetches through `lib/api.ts`.
- **Money: `Decimal` only, stored as INTEGER cents on SQLite via `app/money.py`.** Never float, never computed on the client and sent back.
- Never: commit secrets, log tokens, trust a client-supplied amount, mutate a captured payment, let AI write a financial field.
- Ask first: schema changes, new dependencies, payment-flow changes, CORS changes.

## 8. Definition of done (per task)

- [ ] Acceptance criteria met, verifiable by command
- [ ] Tests written first and passing
- [ ] Skill set declared and applied
- [ ] Audit `VERDICT: PASS`
- [ ] Committed
