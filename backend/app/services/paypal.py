"""PayPal Orders v2 client. Server-side only.

Every fact here was read from PayPal's own documentation on 2026-11-09, not from
memory:

- Token: `https://developer.paypal.com/api/rest/authentication/`
  `POST /v1/oauth2/token`, HTTP Basic over `client_id:client_secret`,
  `grant_type=client_credentials`, response `access_token` + `expires_in`.
- Capture: `https://developer.paypal.com/api/orders/v2/orders-capture`
  server URL `https://api-m.sandbox.paypal.com/`, `POST
  /v2/checkout/orders/{id}/capture`. The documented response carries no `payee`
  on the capture object — see docs/16 A-2 and `_extract_payee`.

Three rules this module exists to enforce:

1. **A verified response is the only proof of payment.** Every assertion lives
   in `capture()`; a `201` with an unexpected shape raises.
2. **A capture is never retried.** It may already have moved money
   (docs/04 §5). One attempt, then a typed failure the caller can reconcile.
3. **The secret never reaches a message.** `httpx` embeds the request in
   `HTTPStatusError.__str__`, and the Basic header is base64, not encryption —
   so every error is raised `from None` with a message built from the status
   code alone.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

import httpx

# `_monotonic` is module-level so a test can drive the clock without sleeping.
_monotonic = time.monotonic

_TOKEN_PATH = "/v1/oauth2/token"
_ORDERS_PATH = "/v2/checkout/orders"

BASE_URLS = {
    "sandbox": "https://api-m.sandbox.paypal.com",
    "live": "https://api-m.paypal.com",
}

# Mirrors `expires_in` semantics: the token is considered dead a minute early so
# a long request cannot expire mid-flight.
_EXPIRY_SKEW_SECONDS = 60


class PayPalError(Exception):
    """Base for every failure this client raises."""


class PayPalRejected(PayPalError):
    """A 4xx: bad credentials, bad request, or a declined capture."""


class PayPalUnavailable(PayPalError):
    """Transport failure or 5xx after the single attempt.

    `attempted` is the difference between "nothing was sent, retry freely" and
    "the capture may have moved money, reconcile instead" (docs/04 §5). The
    caller cannot recover that from a message, so the class carries it.
    """

    def __init__(self, message: str, *, attempted: bool = True) -> None:
        # Defaults to True: assuming money may have moved is the safe error.
        self.attempted = attempted
        super().__init__(message)


class PayPalVerificationError(PayPalError):
    """The response did not prove what it was asked to prove."""


@dataclass(frozen=True, slots=True)
class PayPalCredentials:
    client_id: str
    # `repr=False`: a credential that prints is a credential in a log line, a
    # Sentry frame-locals dump, or a `pytest --showlocals` transcript.
    client_secret: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class CreatedOrder:
    order_id: str
    approve_url: str


@dataclass(frozen=True, slots=True)
class CaptureResult:
    capture_id: str
    payee_merchant_id: str | None
    status: str


class Transport(Protocol):
    def __call__(self, method: str, url: str, headers: dict, body: Any) -> dict: ...


def _default_transport(method: str, url: str, headers: dict, body: Any) -> dict:
    kwargs: dict = {"headers": headers, "timeout": 20.0}
    if isinstance(body, dict):
        kwargs["json"] = body
    else:
        kwargs["content"] = body
    response = httpx.request(method, url, **kwargs)
    response.raise_for_status()
    return response.json()


class PayPalClient:
    def __init__(
        self,
        credentials: PayPalCredentials,
        *,
        env: str = "sandbox",
        transport: Transport | None = None,
    ) -> None:
        if env not in BASE_URLS:
            raise ValueError(f"unknown PayPal environment: {env}")
        self._credentials = credentials
        self._base = BASE_URLS[env]
        self._transport = transport or _default_transport
        self._token: str | None = None
        self._token_refresh_at: float = 0.0

    # ------------------------------------------------------------------ token

    def _access_token(self) -> str:
        if self._token is not None and _monotonic() < self._token_refresh_at:
            return self._token

        basic = base64.b64encode(
            f"{self._credentials.client_id}:{self._credentials.client_secret}".encode()
        ).decode()
        body = self._request(
            "POST",
            f"{self._base}{_TOKEN_PATH}",
            {
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            "grant_type=client_credentials",
            # `attempted=False`: if the token call fails, nothing was captured.
            attempted=False,
        )
        token = body.get("access_token")
        if not token:
            raise PayPalVerificationError("the token response carried no access_token")
        expires_in = int(body.get("expires_in") or 0)
        self._token = token
        self._token_refresh_at = _monotonic() + max(expires_in - _EXPIRY_SKEW_SECONDS, 30)
        return token

    # ----------------------------------------------------------------- orders

    def create_order(
        self,
        *,
        amount: Decimal,
        currency: str,
        reference_id: str,
        custom_id: str,
        return_url: str,
        cancel_url: str,
    ) -> CreatedOrder:
        """The amount is whatever the caller read from the database.

        The body has no amount field of its own on the way in, and this method
        formats it as a two-decimal string: `"120.00"`, never `120.0` and never
        integer cents (docs/04 §4.1).
        """
        body = self._request(
            "POST",
            f"{self._base}{_ORDERS_PATH}",
            self._auth_headers(),
            {
                "intent": "CAPTURE",
                "purchase_units": [
                    {
                        "reference_id": reference_id,
                        "custom_id": custom_id,
                        "amount": {"currency_code": currency, "value": f"{amount:.2f}"},
                    }
                ],
                "application_context": {
                    "return_url": return_url,
                    "cancel_url": cancel_url,
                    "user_action": "PAY_NOW",
                },
            },
            attempted=True,
        )

        order_id = body.get("id")
        if not order_id:
            raise PayPalVerificationError("the order response carried no id")
        approve_url = next(
            (
                link.get("href")
                for link in body.get("links") or []
                if isinstance(link, dict) and link.get("rel") == "approve"
            ),
            None,
        )
        if not approve_url:
            # An order with no approve link cannot be paid. Reporting success
            # would render a Pay button that does nothing.
            raise PayPalVerificationError("the order response carried no approve link")
        return CreatedOrder(order_id=str(order_id), approve_url=str(approve_url))

    # ---------------------------------------------------------------- capture

    def show_order(self, order_id: str) -> dict:
        """`GET /v2/checkout/orders/{id}`.

        Exists because docs/04 §5 mandates "fetch order status first; never
        blind-retry" after a timeout, and that instruction needs a method.
        """
        return self._request(
            "GET",
            f"{self._base}{_ORDERS_PATH}/{order_id}",
            self._auth_headers(),
            None,
            attempted=True,
        )

    def capture(
        self, *, order_id: str, expected_amount: Decimal, expected_currency: str
    ) -> CaptureResult:
        """One attempt. No retry: see the module docstring."""
        body = self._request(
            "POST",
            f"{self._base}{_ORDERS_PATH}/{order_id}/capture",
            self._auth_headers(),
            {},
            attempted=True,
        )

        if body.get("status") != "COMPLETED":
            raise PayPalVerificationError(f"capture status was {body.get('status')!r}")
        if body.get("id") != order_id:
            raise PayPalVerificationError("capture response was for a different order")

        capture = self._single_capture(body)
        if capture.get("status") != "COMPLETED":
            raise PayPalVerificationError(f"capture object status was {capture.get('status')!r}")

        money = capture.get("amount") or {}
        if money.get("currency_code") != expected_currency:
            raise PayPalVerificationError("capture currency did not match the agreement")
        try:
            captured = Decimal(str(money.get("value")))
        except (ArithmeticError, ValueError) as exc:
            raise PayPalVerificationError("capture amount was not a number") from exc
        if captured != expected_amount:
            raise PayPalVerificationError(
                f"captured {captured} but the milestone is {expected_amount}"
            )

        capture_id = capture.get("id")
        if not capture_id:
            raise PayPalVerificationError("capture response carried no capture id")

        return CaptureResult(
            capture_id=str(capture_id),
            payee_merchant_id=_extract_payee(capture),
            status="COMPLETED",
        )

    # -------------------------------------------------------------- internals

    def _auth_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._access_token()}",
            "Content-Type": "application/json",
        }

    def _request(
        self, method: str, url: str, headers: dict, body: Any, *, attempted: bool = False
    ) -> dict:
        try:
            return self._transport(method, url, headers, body)
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code == 401:
                # A revoked or rotated token must not be cached for hours while
                # every call 401s. Drop it; the next call refetches.
                self._token = None
                self._token_refresh_at = 0.0
            # `from None`: the chained exception carries the request, and the
            # request carries the Basic header.
            if 500 <= code < 600:
                raise PayPalUnavailable(
                    f"PayPal returned {code}", attempted=attempted
                ) from None
            raise PayPalRejected(f"PayPal rejected the request ({code})") from None
        except (httpx.RequestError, TimeoutError) as exc:
            raise PayPalUnavailable(
                f"PayPal was unreachable ({type(exc).__name__})", attempted=attempted
            ) from None

    @staticmethod
    def _single_capture(body: dict) -> dict:
        """Exactly one capture object, or a refusal.

        A second capture object means money moved twice. Taking `[0]` and moving
        on would record one id and silently lose the other — and the second id
        never reaches the UNIQUE column that would have caught it.
        """
        try:
            captures = body["purchase_units"][0]["payments"]["captures"]
        except (KeyError, IndexError, TypeError) as exc:
            raise PayPalVerificationError(
                "capture response carried no capture object"
            ) from exc
        if not isinstance(captures, list) or len(captures) != 1:
            raise PayPalVerificationError(
                "capture response carried more than one capture"
            )
        return captures[0]


def _extract_payee(capture: dict) -> str | None:
    """A-2: the documented capture response has no `payee`.

    So this never invents one. If PayPal starts returning it, the id is surfaced
    and the caller can display it as evidence; if it does not, `None` is the
    honest answer and the audit row records that the field was absent.
    """
    payee = capture.get("payee")
    if not isinstance(payee, dict):
        return None
    merchant_id = payee.get("merchant_id")
    return str(merchant_id) if merchant_id else None
