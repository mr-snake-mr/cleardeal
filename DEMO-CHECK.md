# Demo check sheet

For the person **checking** the demo, not the person giving it. `DEMO.md` is the
script; this is the marking scheme.

One row = one claim. Each row says what must be on screen, and how to confirm it
**without believing the screen**. If a row's counter-check fails while the screen
looks right, the demo is wrong and the screen is decoration.

Exact strings below are copied from the code, not from memory: button labels from
`backend/app/static/index.html`, messages from `backend/app/routers/`.

---

## 1. Pre-flight — 60 seconds, before the audience

From `hacko\cleardeal`:

```powershell
.\verify.ps1                     # lint + migration round trip + suite
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

Then open <http://127.0.0.1:8000/health>.

| # | Check | PASS looks like | If not |
|---|---|---|---|
| 1 | `verify.ps1` last line | `VERIFIED` | Stop. Do not demo a red build. |
| 2 | `/health` HTTP status | `200` | `503` = database unreachable or migrations pending |
| 3 | `/health` → `migrations` | a revision hash, e.g. `4f2a9c1e7b30` | `pending` / `none` / `error` → run `alembic -c alembic.ini upgrade head` |
| 4 | `/health` → `paypal_env` | `sandbox` | if `live`, say so out loud before touching money |
| 5 | `pytest` output | no `failed`, no `error`; **at most one** `skipped` | `test_ui_smoke.py` skips itself when Playwright or its chromium is missing — that one skip is honest. Any other skip is a test you are not running. |

`migrations: "pending"` is not a warning, it is a stop: `health.py` returns `503`
on purpose, because a service that cannot say which schema it is running is about
to fail.

**AI key:** without `GEMINI_API_KEY` (or the configured equivalent), *Analyse
brief* and *Draft agreement* return `503` and write nothing. Check now, not at
0:50 — see `config.py`.

---

## 2. During the demo — one row per claim

Sign in first. The page ships prefilled: `demo@cleardeal.test` /
`correct horse battery staple`, project `Marketing site`, client
`client@example.test`, budget `300.00`, and the five-page brief.

| At | Press | On screen it must say | Counter-check that does not trust the screen |
|---|---|---|---|
| 0:20 | **Analyse brief** | a requirements table; every row `explicit` or `inferred`; explicit rows carry a quote from the brief | `test_an_explicit_requirement_without_a_quote_is_rejected` and `test_an_explicit_requirement_quoting_text_not_in_the_brief_is_rejected` — a quote that is not in the brief does not count either |
| 0:50 | **Draft agreement from accepted requirements** | `content intact: true` and `Milestones total $300.00 against a budget of $300.00 — equal to the cent, or the draft is refused.` | The model never checks its own arithmetic: a mismatch returns `502 AI_INVALID_OUTPUT`, *"its milestones did not add up to the budget — and was discarded. Nothing was written."* (`routers/agreements.py`) |
| 1:20 | **Send to both parties**, then **Approve as freelancer** | pill goes `DRAFT` → `PENDING_CLIENT`; then `Freelancer approval recorded.` | **Approve as client (own session only)** must *refuse* here: `this account may not sign as client`. If it signs, the A-15 hole is open |
| 1:20 | **Invite the client** → open the link in a **private window** | `Client link, single use, expires in 72 hours` | The link is not a password: it only works while it is valid and unused |
| 1:45 | **Create order** on a milestone | `Order …` + `Approve in PayPal` + `I approved it — capture` | Amount is read from the milestone row; the request body has no amount field. Without credentials: `MERCHANT_NOT_CONNECTED` — *"Connect a PayPal merchant account first."* |
| 1:45 | **I approved it — capture** | `Captured $300.00 · CAPTURED`, and that milestone's row switches to `PAID` | money is marked only after PayPal confirms. A 201 whose total disagrees sets `RECONCILIATION_REQUIRED`, never `CAPTURED` |
| 2:15 | type the client's ask → **Check against the approved scope** | `OUT_OF_SCOPE` with a quoted sentence and its section key | The quote must appear *inside* the cited section, and the section id must be real. Both are enforced server-side |

---

## 3. The negative proof — this is the product

Anyone can show a green verdict. The claim worth checking is that a **bad
citation is refused**. Quote a test name at the audience; it is the spec:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q tests/test_changes.py
```

The test names are the argument, and they read as sentences:

- `test_a_citation_to_a_section_that_does_not_exist_is_downgraded`
- `test_a_quote_stolen_from_another_section_is_downgraded`
- `test_a_reworded_quote_is_downgraded`
- `test_a_quote_that_stops_mid_word_is_downgraded`
- `test_one_bad_citation_downgrades_the_item_even_when_another_resolves`
- `test_confidence_cannot_be_a_number`

On stage, the copywriting ask is the live example: the brief says copywriting is
**not** included, so the citation is refused and the verdict is
`NEEDS_CLARIFICATION` with empty evidence. That is the rule working, not a bug.

---

## 4. One command that answers "is it real"

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q tests/test_e2e_golden_path.py
```

One test, the whole path — brief → requirements → draft → two approvals →
scope check, with a real citation accepted and a fabricated one refused in the
same response. Only the model provider is stubbed; routing, validation,
persistence and the evidence rule are the shipping code.

---

## 5. What cannot be checked, and must be said first

Say these before anyone asks. They are in `docs/16` with how to close each.

| Claim | Status |
|---|---|
| Live PayPal capture | **not run** — needs sandbox credentials (A-1, A-2) |
| Live Gemini call | **not run** — no API key has been used |
| Funds reaching a named payee | **unconfirmed** — the capture response's payee field has never been inspected |
| Change approval into `v(n+1)` | **not built** — needs a pricing decision, `docs/13` |
| Escrow, payouts, platform fee, multiparty | **not built** — needs PayPal partner approval, `docs/04` |
| `render.yaml` deploy | **never run** |

---

## 6. If something fails mid-demo

| Symptom | Truthful line | Do not |
|---|---|---|
| Analysis returns `503` | "The drafting service is unavailable. Nothing was written." | Retry on stage or fake a response |
| Order create returns `MERCHANT_NOT_CONNECTED` | "No merchant connected — that is the honest state." | Paste a fake order id |
| Capture returns `RECONCILIATION_REQUIRED` | "PayPal's total disagreed with the milestone, so it is not marked paid." | Call it a success |
| Invitation link rejected | "That invitation cannot be used: `<reason>`." | Sign as the client yourself |

The fallback that always holds: `git status` clean, `.\verify.ps1` printing
`VERIFIED`.
