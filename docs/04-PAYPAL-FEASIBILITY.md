# PayPal Technical Feasibility Report

**Conclusion first:** a standard Orders v2 integration **cannot** pay a freelancer from a client in one hop unless the freelancer is the merchant of record. Multiparty — the correct production answer — requires PayPal approval and cannot ship in three days. The build must therefore choose a merchant model explicitly and state what it does not do.

All facts fetched from official PayPal documentation on 2026-11-09. Anything unverified is in [16](16-UNVERIFIED-ASSUMPTIONS.md).

---

## 1. Verified capability inventory

### 1.1 Orders v2

Source: [developer.paypal.com/api/orders/v2/](https://developer.paypal.com/api/orders/v2/)

| Endpoint | Purpose |
|---|---|
| `POST /v2/checkout/orders` | Create |
| `GET /v2/checkout/orders/{id}` | Show |
| `PATCH /v2/checkout/orders/{id}` | Update while `CREATED`/`APPROVED` |
| `POST /v2/checkout/orders/{id}/confirm-payment-source` | Confirm payer intent |
| `POST /v2/checkout/orders/{id}/authorize` | Authorize |
| `POST /v2/checkout/orders/{id}/capture` | **Capture** |
| `POST /v2/checkout/orders/{id}/track` | Tracking |

Sandbox host, from the official webhook sample: `https://api.sandbox.paypal.com`.

Three states are produced by this API. A fourth must never be claimed:

```
order CREATED  ->  order APPROVED  ->  capture COMPLETED  (money moved to payee)
"disbursed to a second party" is NOT a state this API produces.
```

### 1.2 Webhooks

Sources: [overview](https://developer.paypal.com/api/rest/webhooks/), [integrate](https://developer.paypal.com/api/rest/webhooks/rest/)

| Fact | Consequence |
|---|---|
| HTTPS on port 443 required | Render provides it; local dev needs a tunnel |
| Up to 10 URLs per app; `*` subscribes all | One endpoint, explicit event list |
| Non-2xx → retry **25 times over 3 days** | Handlers must be idempotent, not "usually fast" |
| Two verification methods: CRC32+RSA self-verify, or postback to `verify-webhook-signature` | Self-verify preferred by the docs |
| Signed string: `transmissionId\|timeStamp\|webhookId\|crc32` | Built from headers + the **raw** body |
| "Do not parse the body to an object and re-stringify it" | Capture raw bytes before JSON decoding |
| Headers: `paypal-transmission-id`, `-time`, `-cert-url`, `-sig` | Cert fetched and cached from `paypal-cert-url` |
| Simulator events use webhook id `WEBHOOK_ID`; **postback verification is unsupported for them** | Local tests must self-verify |

### 1.3 Multiparty / marketplaces

Source: [developer.paypal.com/platforms/](https://developer.paypal.com/platforms/) (updated 2026-11-03)

Advertises: onboard global sellers, partner fees, FX, payouts, disputes.

Constraints that decide the architecture:

| Verified constraint | Implication |
|---|---|
| "Test in the sandbox with a standard developer account. Apply for live credentials and go live after your platform or marketplace has been **approved**." | Sandbox exploration only; approval gate for anything real |
| Partner fees via `PURCHASE_UNITS.PAYMENT_INSTRUCTION.PLATFORM_FEES.AMOUNT` | The path exists — subject to the next rows |
| "Partner must be enabled by PayPal to use this feature." | Not self-serve |
| Fee currency must equal transaction currency | Kills mixed-currency demos |
| Fee payee must add a bank account; fees settle daily to it | Not a demo-day flow |
| Partner/live API-caller account "cannot have a PayPal balance" | Account topology constraint |
| "Platform fee is not supported for first-party integrations." | Rules out platform-sells-to-itself |
| Access requested via a "Tell us about your platform" form | Commercial process, days not hours |

### 1.4 Payouts v1

Source: [developer.paypal.com/api/payments.payouts-batch/v1/](https://developer.paypal.com/api/payments.payouts-batch/v1/)

`POST /v1/payments/payouts`, `GET /v1/payments/payouts/{id}`, `GET /v1/payments/payouts-item/{id}`, `POST /v1/payments/payouts-item/{id}/cancel`.

This is how you would pay a freelancer **if the platform held the money**. Requires the holding account, a funding source and Payouts enabled. Roadmap, not v1.

---

## 2. The merchant-of-record question

> Who receives the money when a client pays a milestone?

### Model A — platform is merchant of record

ClearDeal's sandbox business account receives the payment; the ledger attributes it to a milestone.

Feasible today, no approval. But the platform is holding the freelancer's money with no disbursement — money transmission in production. Judge risk: high.

### Model B — bring your own merchant (RECOMMENDED)

Each freelancer registers a sandbox business account, creates a REST app, and pastes `client_id` / `client_secret`. Orders are created and captured **with that merchant's credentials**, so the money lands in their account.

- Feasible, no approval, no platform fee, no payout, no escrow.
- Honest — *provided A-2 holds*: the capture response is expected to carry the payee, letting ClearDeal display `payee.merchant_id` and show it matching the bound freelancer. That would be evidence rather than a claim, but the field is unconfirmed until the first real capture is inspected.
- Cost: ClearDeal stores third-party credentials → AES-GCM at rest, never logged, never returned, scoped to order create/read/capture.
- Judge question to expect: *"why store seller secrets?"* Answer: *"because multiparty onboarding needs PayPal partner approval, and this is the only model where the money genuinely reaches the freelancer; production replaces it with OAuth seller onboarding."*

### Model C — multiparty (correct, unavailable)

Onboard sellers, take a partner fee, pay out. Not in three days: approval, partner enablement, KYC, bank topology. Documented as the production path — which demonstrates judgement.

### Decision

**Model B primary. Model A behind one config switch as fallback.** The abstraction:

```python
class MerchantCredentials(Protocol):
    def resolve(self, project: Project) -> PayPalCredentials: ...

class PerTenantCredentials: ...   # Model B
class PlatformCredentials: ...    # Model A
```

---

## 3. What ClearDeal will and will not claim

| Claim | Permitted |
|---|---|
| Orders created and captured server-side against Sandbox | Yes |
| Capture verified with PayPal before marking paid | Yes |
| Webhook signatures verified, events idempotent | Yes |
| The freelancer's merchant account received the payment | Yes, Model B only — **and only after A-2 is confirmed**, with the captured payee displayed |
| ClearDeal holds funds in escrow | **No** |
| Automatic payouts to freelancers | **No** |
| ClearDeal takes a platform fee | **No** |
| Marketplace payments supported | **No** |

---

## 4. Implementation contract

### 4.1 Order creation (server-side only)

```
POST /v2/checkout/orders      Authorization: Bearer <merchant token>
intent: CAPTURE
purchase_units[0].amount       = { currency_code: USD, value: "100.00" }  # from DB, never the client
purchase_units[0].reference_id = milestone UUID
purchase_units[0].custom_id    = "{project_id}:{milestone_id}:{agreement_version}"
application_context            = { return_url, cancel_url, user_action: "PAY_NOW" }
```

Amount is read from `milestones.amount` inside a transaction; the request carries no amount. Order id, merchant id and version are persisted **before** the button appears. Retries reuse an existing `CREATED` order rather than creating a second (whether PayPal also supports an idempotency header is A-3).

### 4.2 Capture and verification

1. JS SDK returns `orderID` to the client.
2. Client calls `POST /api/v1/payments/{payment_id}/capture`.
3. Server asserts the payment is `CREATED`/`APPROVED` and the amount matches the database.
4. Server calls `POST /v2/checkout/orders/{order_id}/capture`.
5. Server asserts `status == "COMPLETED"`, amount, currency and order id — plus the payee merchant id **only when the response carries one** (A-2).
6. Only then: `CAPTURED`, audit event, store capture id and response.
7. Any mismatch → `RECONCILIATION_REQUIRED`, surfaced in the timeline, never "paid".

Capture success is not the same as our record being written: if step 5 succeeds and the database write fails, the payment must converge on the next webhook or reconcile — never left unknown, never captured twice.

### 4.3 Webhook handler, in order

1. Read **raw bytes** before any parsing.
2. Verify the signature (self-verify, RSA-SHA256 against the cached cert).
3. Reject → `400`, log the transmission id, change nothing.
4. `INSERT webhook_events(event_id) ON CONFLICT DO NOTHING` — a duplicate returns `200` immediately. **This is the idempotency gate.**
5. Reconcile the capture id against `payments`, compare amounts, update state.
6. Return `200` fast; do slow work after commit.

Subscribe explicitly to `PAYMENT.CAPTURE.COMPLETED`, `PAYMENT.CAPTURE.DENIED`, `PAYMENT.CAPTURE.REFUNDED`, `CHECKOUT.ORDER.APPROVED`.

### 4.4 Environment

```
PAYPAL_ENV=sandbox             # Literal["sandbox","live"]
PAYPAL_CLIENT_ID=
PAYPAL_CLIENT_SECRET=          # never logged, never returned
PAYPAL_WEBHOOK_ID=
CREDENTIALS_ENCRYPTION_KEY=    # Model B only
```

---

## 5. Failure modes

| Failure | Required behaviour |
|---|---|
| Token request fails | `502`, payment stays `CREATED`, retry offered |
| Order creation fails | No row advanced; sanitised error |
| Client abandons checkout | `CREATED`; order reusable until expiry |
| Capture declined | `FAILED`, retry offered, no second order |
| Capture succeeds, DB write fails | `RECONCILIATION_REQUIRED`; converge via webhook or job; never re-capture |
| Webhook before capture response | Both converge on the capture id; one row |
| Duplicate webhook | `200`, zero new rows |
| Invalid signature | `400`, zero state change |
| Amount mismatch | `RECONCILIATION_REQUIRED`, block paid UI |
| Network timeout on capture | Fetch order status first; never blind-retry |

A database status update alone is never proof of payment. Only a verified PayPal response is.

---

## 6. Sandbox checklist

1. Developer account → Sandbox → Accounts. PayPal usually provisions one Personal and one Business account already — **check before creating anything**.
2. For each business account: Apps & Credentials → create REST app → record `client_id`, `client_secret`.
3. Subscribe the webhook URL to the explicit event list; record the **webhook id**.
4. Confirm USD works on the accounts.
5. Deploy to HTTPS before testing webhooks — PayPal will not deliver to localhost.
6. Verify one **real** delivery; simulator events cannot be postback-verified.

## 7. Open items

1. **Model B or A?** Recommendation: B (two extra hours).
2. **Currency:** decided — USD only.
3. **Second sandbox merchant available?** Determines whether B is possible at all (A-1).
