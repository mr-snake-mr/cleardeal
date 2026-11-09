# Frontend Structure

Vite · React 18 · TypeScript `strict` · Tailwind 3.4 · TanStack Query. No component library — a component library that arrives with its own visual language makes a hackathon app look like a hackathon app.

## 1. Routes

| Route | Audience | Purpose |
|---|---|---|
| `/login`, `/register` | Freelancer | Session |
| `/` | Freelancer | Project list + new project |
| `/projects/:id` | Freelancer | Analysis, agreement, milestones, payment |
| `/projects/:id/changes` | Freelancer | Scope check, change proposals |
| `/projects/:id/timeline` | Freelancer | Audit trail |
| `/settings/paypal` | Freelancer | Connect merchant credentials |
| `/review/:token` | Client | Magic-link review: read agreement, approve/reject |

**Two surfaces, not one page with a role toggle.** The client surface has no sidebar, no project list, no navigation. It shows one agreement and two buttons. A role toggle would be a tell.

## 2. Directory shape

```
frontend/
  src/
    main.tsx                router + QueryClient
    App.tsx                 shell: nav, <Outlet/>, error boundary
    routes/                 one file per route
      ProjectsList.tsx
      ProjectDetail.tsx
      ProjectChanges.tsx
      ProjectTimeline.tsx
      ReviewAgreement.tsx
      SettingsPaypal.tsx
      Login.tsx  Register.tsx
    components/
      ui/                   Button, Input, Select, Card, Badge, Dialog,
                            Skeleton, EmptyState, ErrorState, Toast
      agreement/            AgreementView, SectionBlock, MilestoneTable, SumBar
      scope/                ScopeRequestForm, ScopeItemCard, EvidenceQuote,
                            ClassificationBadge, ImpactRow
      payment/              PayButton, PaymentStatus, PayeeProof
      timeline/             TimelineList, TimelineEvent
    lib/
      api.ts                typed fetch wrapper, the ONLY place fetch is called
      queries.ts            TanStack Query hooks, one per endpoint
      format.ts             money and date formatting (single source)
      types.ts              generated-ish mirrors of response schemas
    styles/
      tokens.css            CSS custom properties from §5
      index.css             Tailwind layers
```

Rule: **`fetch` appears exactly once in the codebase**, inside `lib/api.ts`. A `fetch` in a component is a review failure, because that is where error handling, auth and base URL drift apart.

## 3. Data layer

```ts
// lib/api.ts
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${import.meta.env.VITE_API_URL}/api/v1${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!res.ok) throw await ApiError.fromResponse(res);   // {code, message, request_id, details}
  return res.status === 204 ? (undefined as T) : res.json();
}
```

`ApiError` carries `code`. Components branch on `code`, never on a status number or a message string. That is what makes `STALE_VERSION` show a "reload the newer version" affordance while every other `409` shows a generic conflict.

### Query keys

```ts
["projects"]
["project", id]
["agreement", versionId]
["changes", projectId]
["change", changeId]
["payments", milestoneId]
["timeline", projectId]
["health"]
```

Mutations invalidate by prefix. After a capture, `["payments", id]` and `["timeline", projectId]` both invalidate — the timeline must not lag behind the payment badge on camera.

## 4. The signature screen

`ScopeItemCard` is the one component a judge will look at hardest.

```
┌──────────────────────────────────────────────────────────┐
│ Google login                              OUT OF SCOPE   │  ← ClassificationBadge
│                                                          │
│ ┌ Evidence ─────────────────────────────────────────┐    │  ← EvidenceQuote
│ │ §AUTH                                              │    │
│ │ "Sign-in is email and password only. Social        │    │
│ │  sign-in is excluded."                             │    │
│ └────────────────────────────────────────────────────┘    │
│                                                          │
│ 4–6 h    +$120.00    +2 days        confidence: high     │  ← ImpactRow
│ [ Adjust ]  [ Include in proposal ]                      │
└──────────────────────────────────────────────────────────┘
```

Requirements:
- The quote renders as a **quote with its section key**, monospaced on a tinted panel, visually distinct from the model's explanation. A judge must be able to see which part came from the agreement and which part came from the AI.
- `NEEDS_CLARIFICATION` renders **no** impact row and no cost, and its evidence panel says "No section addresses this — human decision required." An empty panel would look like a bug; an explicit sentence looks like a decision.
- `ALREADY_INCLUDED` shows the evidence and *no* price, with the label "Included in v2 — no charge".

## 5. Design tokens

Defined once in `styles/tokens.css`, consumed by Tailwind via `theme.extend`. No raw hex in a component.

```css
:root {
  /* surface */
  --bg:            #FAFAF9;   /* page */
  --surface:       #FFFFFF;   /* card */
  --surface-sunk:  #F4F4F2;   /* quote panels, table stripes */
  --border:        #E4E4E1;
  --border-strong: #C9C9C4;

  /* ink */
  --ink:           #14161A;
  --ink-muted:     #5A5F68;
  --ink-faint:     #8B9099;

  /* semantic — classification only, never decoration */
  --ok:            #16794C;   --ok-bg:      #E7F4ED;
  --warn:          #9A5B00;   --warn-bg:    #FDF1DE;
  --danger:        #A32020;   --danger-bg:  #FBEAEA;
  --info:          #1F4E8C;   --info-bg:    #E8F0FA;

  /* type */
  --font-ui:   "Inter", system-ui, -apple-system, sans-serif;
  --font-quote:"JetBrains Mono", ui-monospace, SFMono-Regular, monospace;
  --fs-xs: 12px; --fs-sm: 13px; --fs-base: 15px;
  --fs-lg: 18px; --fs-xl: 22px; --fs-2xl: 28px;
  --lh-tight: 1.25; --lh-base: 1.55;

  /* space — 4px base */
  --s-1: 4px;  --s-2: 8px;  --s-3: 12px; --s-4: 16px;
  --s-5: 20px; --s-6: 24px; --s-8: 32px; --s-10: 40px; --s-12: 48px;

  /* radius, elevation */
  --r-sm: 4px; --r-md: 8px; --r-lg: 12px;
  --shadow-sm: 0 1px 2px rgba(20,22,26,.06);
  --shadow-md: 0 4px 12px rgba(20,22,26,.08);
  --focus: 0 0 0 3px rgba(31,78,140,.35);
}
```

Colour is information. The four semantic colours appear **only** on classification, payment state, and errors. Nothing else is green because it looks nice — if a judge sees green, it means something passed.

Type scale is six sizes, not eleven. Spacing is a 4px grid, not arbitrary values.

## 6. State handling — the three states are not optional

Every asynchronous region renders one of four things, and the type system enforces it:

```ts
type Async<T> =
  | { state: "loading" }
  | { state: "empty" }
  | { state: "error"; error: ApiError }
  | { state: "ready"; data: T };
```

| State | Requirement |
|---|---|
| Loading | `Skeleton` matching the final layout's dimensions — no spinner that collapses the page when it resolves |
| Empty | A sentence explaining what would be here and the one action that creates it |
| Error | The `code`-specific message, the `request_id` in small mono, and a Retry button |
| Ready | The content |

No region may render `null` while loading. A blank area during a demo reads as "broken".

## 7. Payment surface

`PayButton` does three things and is honest about all of them:

1. Calls `POST /milestones/{id}/payment` with an `Idempotency-Key` (generated once per mount with `crypto.randomUUID()`, stored in a ref — not per click).
2. Loads the PayPal JS SDK with the returned `paypal_order_id` and `paypal_client_id` (08 §5 — the client id is public by PayPal's design; the secret never leaves the server), renders the button.
3. On approval, calls `capture` and then renders `PaymentStatus`.

`PaymentStatus` for `CAPTURED` shows: amount, milestone title, capture id, and — **only when present** — the verified payee merchant id labelled "Paid to (PayPal-verified)".

If `payee_merchant_id` is null (assumption A-2 unconfirmed), the component does **not** render an empty row and does **not** claim the money reached the freelancer. It shows "Paid — capture verified" and nothing stronger. The UI must never assert more than the API returned.

`RECONCILIATION_REQUIRED` renders a distinct amber panel: "PayPal's record and ours disagree. This payment is on hold pending reconciliation." With the capture id and a link to the timeline. Not a red error — the payment may well be fine; we do not know yet, and the UI says exactly that.

## 8. Accessibility

| Requirement | Check |
|---|---|
| Every interactive element is a `<button>` or `<a>` with an accessible name | `npm run a11y` + manual tab sweep |
| Focus is visible on every control | `--focus` ring, never `outline: none` |
| Classification is not conveyed by colour alone | badge always carries a text label |
| Modal traps focus, `Esc` closes, returns focus to the trigger | `Dialog` component, tested |
| Colour contrast ≥ 4.5:1 for body text | tokens above are chosen to pass; verified once with a contrast tool |
| Motion respects `prefers-reduced-motion` | transitions gated in `index.css` |

## 9. Build and scripts

```json
{
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "typecheck": "tsc --noEmit",
    "a11y": "axe --stdout dist/index.html"
  }
}
```

This block is the **planned** `package.json` for Block 3.1 — `frontend/` is not scaffolded yet, so nothing here is installed.

**Only scripts whose tooling is actually installed.** An earlier version of this file declared `lint` and `test` that pointed at tools not in `devDependencies`; both were deleted rather than left as decoration. If Playwright is added for the E2E suite (12), `test:e2e` appears in the same commit that adds `@playwright/test`.

## 10. Rules that survive review

1. No `any`. `unknown` plus a narrowing parser is the escape hatch, and it must be justified in a comment.
2. No amount formatted anywhere except `lib/format.ts::money()`. Two formatters eventually disagree by a cent in front of a judge.
3. No optimistic update on a payment. The badge changes when the server says so.
4. No dead controls. If a button cannot act, it is not rendered — not disabled with a tooltip. Disabled-with-no-reason is the most common hackathon tell.
5. No mock data path in `src/`. Fixtures live in `backend/tests/fixtures/`, and the replay flag from doc 05 §9 is server-side so the frontend cannot silently lie.
