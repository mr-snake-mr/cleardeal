# Skills ledger

The declaration `AGENTS.md` requires, in the one form a machine can check.

Rewrite both tables at the start of every task, before the first write. Then:

```powershell
.\backend\.venv\Scripts\python.exe tools\skillgate.py
```

`tools/skillgate.py` fails the change if: nothing is applied; fewer than five
skills are ruled out; a named skill is not installed; a reason is shorter than
20 characters or is a placeholder; or a skill appears on both lists. `verify.ps1`
runs it first, so a decorative ledger cannot reach a commit.

Scope of this ledger: the invitation and client-signature flow — one-click copy,
a same-tab accept path, and a client view that hides the freelancer's controls.

## Applied

| Skill | Where it was used, and what it changed |
|---|---|
| `using-superpowers` | Set the order of work: sweep the catalogue, activate, declare, then touch the file. It is why this ledger exists before the edits rather than after them. |
| `frontend-ui-engineering` | Governed the page change: messages on interaction, a visible label for every control, no colour-only state, focus kept on the button that was pressed. |
| `ponytail` | Kept the change to one file and four edits. No new endpoint, no component framework, no "later" code. |
| `karpathy-guidelines` | Forced the assumption into the open: a client view is a presentation change only, and the server's role check must be untouched. Also stopped the fix from growing into a refactor of the page. |
| `accessibility` | Found by `tools/skillscan.py`, not by guessing. Applied to the client view: the banner is a live region, so it is unhidden before its text is written — a `display:none` live region announces nothing. |
| `verification-before-completion` | The claim "the page still works" is only made after `test_frontend.py` and the browser test print a pass, and `ruff` prints `All checks passed`. |

## Ruled out

| Skill | Why it was ruled out |
|---|---|
| `brainstorming` | The affordance and the constraint were both named in the request; there was no design space to open first. |
| `writing-plans` | One file, four edits, no phase boundary worth a plan document. A plan here would be longer than the diff. |
| `spec-driven-development` | The behaviour is already specified — doc 16 A-15 forbids one session signing both sides. A new spec would restate it. |
| `verification-loop` | `verify.ps1` already runs lint, a migration round trip and the suite in this repo. A second loop duplicates a step that exists. |
| `ui-demo` | It records a video of a demo. The complaint was friction inside the flow, not the absence of a recording. |
| `docs-governance` | An integrity router for a large documentation set; one check sheet does not need a router. |
| `api-and-interface-design` | No endpoint changed. The invitation API and its refusals were read and left exactly as they were. |
| `debugging-and-error-recovery` | The one failure was an assertion split across two lines by a template literal, fixed at the source; no repeated-failure cycle required the method. |
| `browser-testing-with-devtools` | Needs the Chrome DevTools MCP, which is not in this session; `test_ui_smoke.py` opens the real page in Playwright instead. |
