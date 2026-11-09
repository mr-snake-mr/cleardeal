"""The PayPal client, with no network.

Sources, fetched 2026-11-09:
- https://developer.paypal.com/api/rest/authentication/ — token is
  `POST /v1/oauth2/token`, HTTP Basic over `client_id:client_secret`,
  `grant_type=client_credentials`, response carries `access_token` and
  `expires_in`.
- https://developer.paypal.com/api/orders/v2/orders-capture — server URL
  `https://api-m.sandbox.paypal.com/`, capture is
  `POST /v2/checkout/orders/{id}/capture`, and the response the reference
  documents carries no `payee` on the capture object (see docs/16 A-2).

Every test drives a fake transport. What is under test is the envelope — token
caching, the exact payload, and the assertions that decide whether money may be
called received. PayPal's own behaviour is not tested here and cannot be.
"""

from __future__ import annotations

import base64
import json
from decimal import Decimal

import httpx
import pytest

from app.services.paypal import (
    CaptureResult,
    CreatedOrder,
    PayPalClient,
    PayPalCredentials,
    PayPalRejected,
    PayPalUnavailable,
    PayPalVerificationError,
)

CREDS = PayPalCredentials(client_id="client-abc", client_secret="secret-xyz")


def _status(code: int, body: dict | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api-m.sandbox.paypal.com/v2/checkout/orders")
    response = httpx.Response(code, request=request, json=body or {})
    return httpx.HTTPStatusError("boom", request=request, response=response)


TOKEN_OK = {"access_token": "A21AA-token", "expires_in": 31668}

ORDER_OK = {
    "id": "5O190127TN364715T",
    "status": "CREATED",
    "links": [
        {
            "href": "https://www.sandbox.paypal.com/checkoutnow?token=5O190127TN364715T",
            "rel": "approve",
        },
        {
            "href": "https://api-m.sandbox.paypal.com/v2/checkout/orders/5O190127TN364715T",
            "rel": "self",
        },
    ],
}

CAPTURE_OK = {
    "id": "5O190127TN364715T",
    "status": "COMPLETED",
    "purchase_units": [
        {
            "reference_id": "milestone-1",
            "payments": {
                "captures": [
                    {
                        "id": "3C679366HH908993F",
                        "status": "COMPLETED",
                        "amount": {"currency_code": "USD", "value": "120.00"},
                    }
                ]
            },
        }
    ],
}


class FakeTransport:
    """Records every call, then replays scripted answers in order."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    def __call__(self, method, url, headers, body):
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        outcome = self.outcomes.pop(0) if self.outcomes else TOKEN_OK
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _client(*outcomes, credentials=CREDS) -> tuple[PayPalClient, FakeTransport]:
    transport = FakeTransport(*outcomes)
    return PayPalClient(credentials, transport=transport), transport


# ------------------------------------------------------------------- the token


def test_the_token_request_matches_the_documented_shape():
    client, transport = _client(TOKEN_OK, ORDER_OK)
    client.create_order(
        amount=Decimal("120.00"),
        currency="USD",
        reference_id="milestone-1",
        custom_id="proj:ms:v1",
        return_url="https://app.test/return",
        cancel_url="https://app.test/cancel",
    )

    token_call = transport.calls[0]
    assert token_call["method"] == "POST"
    assert token_call["url"] == "https://api-m.sandbox.paypal.com/v1/oauth2/token"
    assert token_call["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    expected = base64.b64encode(b"client-abc:secret-xyz").decode()
    assert token_call["headers"]["Authorization"] == f"Basic {expected}"
    assert token_call["body"] == "grant_type=client_credentials"


def test_the_token_is_cached_across_calls():
    """Two orders, one token. A token per request is how a demo gets rate
    limited at the worst possible moment."""
    client, transport = _client(TOKEN_OK, ORDER_OK, ORDER_OK)

    for _ in range(2):
        client.create_order(
            amount=Decimal("120.00"),
            currency="USD",
            reference_id="milestone-1",
            custom_id="proj:ms:v1",
            return_url="https://app.test/return",
            cancel_url="https://app.test/cancel",
        )

    urls = [call["url"] for call in transport.calls]
    assert urls.count("https://api-m.sandbox.paypal.com/v1/oauth2/token") == 1


def test_an_expired_token_is_replaced(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr("app.services.paypal._monotonic", lambda: clock["now"])

    client, transport = _client(TOKEN_OK, ORDER_OK, TOKEN_OK, ORDER_OK)
    client.create_order(
        amount=Decimal("120.00"),
        currency="USD",
        reference_id="m",
        custom_id="c",
        return_url="https://app.test/r",
        cancel_url="https://app.test/c",
    )

    clock["now"] += 31_668 + 1  # past expires_in
    client.create_order(
        amount=Decimal("120.00"),
        currency="USD",
        reference_id="m",
        custom_id="c",
        return_url="https://app.test/r",
        cancel_url="https://app.test/c",
    )

    urls = [call["url"] for call in transport.calls]
    assert urls.count("https://api-m.sandbox.paypal.com/v1/oauth2/token") == 2


def test_a_rejected_token_never_prints_the_secret():
    """httpx puts the request into `HTTPStatusError.__str__`. The Basic header
    is base64, not encryption: a str(exc) that leaks it leaks the credential."""
    client, _ = _client(_status(401))

    with pytest.raises(PayPalRejected) as caught:
        client.create_order(
            amount=Decimal("1.00"),
            currency="USD",
            reference_id="m",
            custom_id="c",
            return_url="https://app.test/r",
            cancel_url="https://app.test/c",
        )

    blob = f"{caught.value} {caught.value!r} {caught.value.__cause__}"
    assert "secret-xyz" not in blob
    assert base64.b64encode(b"client-abc:secret-xyz").decode() not in blob


# ------------------------------------------------------------- the order create


def test_the_order_carries_the_amount_from_the_database_not_the_caller():
    client, transport = _client(TOKEN_OK, ORDER_OK)

    order = client.create_order(
        amount=Decimal("120.00"),
        currency="USD",
        reference_id="milestone-1",
        custom_id="proj:ms:v1",
        return_url="https://app.test/return",
        cancel_url="https://app.test/cancel",
    )

    assert isinstance(order, CreatedOrder)
    assert order.order_id == "5O190127TN364715T"
    assert "checkoutnow" in order.approve_url

    sent = transport.calls[1]
    assert sent["url"] == "https://api-m.sandbox.paypal.com/v2/checkout/orders"
    assert sent["headers"]["Authorization"] == "Bearer A21AA-token"
    assert sent["body"]["intent"] == "CAPTURE"
    unit = sent["body"]["purchase_units"][0]
    # A string with two decimals: "120.00", never 120.0 and never 12000.
    assert unit["amount"] == {"currency_code": "USD", "value": "120.00"}
    assert unit["reference_id"] == "milestone-1"
    assert unit["custom_id"] == "proj:ms:v1"
    assert sent["body"]["application_context"]["user_action"] == "PAY_NOW"


def test_an_order_without_an_approve_link_is_a_rejection():
    """No approve link means no way to pay. Treating it as success would put a
    Pay button on screen that does nothing."""
    client, _ = _client(TOKEN_OK, {"id": "X", "status": "CREATED", "links": []})

    with pytest.raises(PayPalVerificationError):
        client.create_order(
            amount=Decimal("1.00"),
            currency="USD",
            reference_id="m",
            custom_id="c",
            return_url="https://app.test/r",
            cancel_url="https://app.test/c",
        )


# ------------------------------------------------------------------ the capture


def test_a_matching_capture_is_returned_with_its_capture_id():
    client, transport = _client(TOKEN_OK, CAPTURE_OK)

    result = client.capture(
        order_id="5O190127TN364715T", expected_amount=Decimal("120.00"), expected_currency="USD"
    )

    assert isinstance(result, CaptureResult)
    assert result.capture_id == "3C679366HH908993F"
    assert result.payee_merchant_id is None  # A-2: absent in the documented shape

    sent = transport.calls[-1]
    assert sent["url"] == (
        "https://api-m.sandbox.paypal.com/v2/checkout/orders/5O190127TN364715T/capture"
    )


def test_a_payee_is_surfaced_when_the_response_carries_one():
    """A-2 is unverified, so the client must handle both worlds without a code
    change on demo day: present → report it, absent → say so, never guess."""
    body = json.loads(json.dumps(CAPTURE_OK))
    body["purchase_units"][0]["payments"]["captures"][0]["payee"] = {"merchant_id": "M123"}

    client, _ = _client(TOKEN_OK, body)

    result = client.capture(
        order_id="5O190127TN364715T", expected_amount=Decimal("120.00"), expected_currency="USD"
    )

    assert result.payee_merchant_id == "M123"


@pytest.mark.parametrize("value,currency", [("100.00", "USD"), ("120.00", "EUR")])
def test_an_amount_or_currency_mismatch_blocks_the_capture(value, currency):
    body = json.loads(json.dumps(CAPTURE_OK))
    capture = body["purchase_units"][0]["payments"]["captures"][0]
    capture["amount"] = {"currency_code": currency, "value": value}
    client, _ = _client(TOKEN_OK, body)

    with pytest.raises(PayPalVerificationError):
        client.capture(
            order_id="5O190127TN364715T",
            expected_amount=Decimal("120.00"),
            expected_currency="USD",
        )


@pytest.mark.parametrize("status", ["PENDING", "DECLINED", "PARTIALLY_REFUNDED"])
def test_a_capture_that_is_not_completed_is_never_paid(status):
    body = json.loads(json.dumps(CAPTURE_OK))
    body["status"] = status
    client, _ = _client(TOKEN_OK, body)

    with pytest.raises(PayPalVerificationError):
        client.capture(
            order_id="5O190127TN364715T",
            expected_amount=Decimal("120.00"),
            expected_currency="USD",
        )


def test_a_capture_for_a_different_order_is_refused():
    body = json.loads(json.dumps(CAPTURE_OK))
    body["id"] = "SOMEOTHERORDER"
    client, _ = _client(TOKEN_OK, body)

    with pytest.raises(PayPalVerificationError):
        client.capture(
            order_id="5O190127TN364715T",
            expected_amount=Decimal("120.00"),
            expected_currency="USD",
        )


def test_a_response_with_no_capture_object_is_refused():
    """A 201 with an unexpected shape is not a payment. The absence of the
    capture id is the difference between 'paid' and 'maybe'."""
    client, _ = _client(TOKEN_OK, {"id": "5O190127TN364715T", "status": "COMPLETED"})

    with pytest.raises(PayPalVerificationError):
        client.capture(
            order_id="5O190127TN364715T",
            expected_amount=Decimal("120.00"),
            expected_currency="USD",
        )


# -------------------------------------------------------------------- transport


def test_a_transport_failure_is_unavailable_and_capture_is_not_retried():
    """docs/04 §5: never blind-retry a capture. It may already have moved money."""
    client, transport = _client(TOKEN_OK, httpx.ConnectError("no route"))

    with pytest.raises(PayPalUnavailable):
        client.capture(
            order_id="5O190127TN364715T",
            expected_amount=Decimal("120.00"),
            expected_currency="USD",
        )

    captures = [c for c in transport.calls if c["url"].endswith("/capture")]
    assert len(captures) == 1


def test_a_five_hundred_is_unavailable_not_a_rejection():
    client, _ = _client(TOKEN_OK, _status(503))

    with pytest.raises(PayPalUnavailable):
        client.capture(
            order_id="5O190127TN364715T",
            expected_amount=Decimal("120.00"),
            expected_currency="USD",
        )


def test_a_four_hundred_is_a_rejection_not_an_outage():
    client, _ = _client(TOKEN_OK, _status(422))

    with pytest.raises(PayPalRejected):
        client.capture(
            order_id="5O190127TN364715T",
            expected_amount=Decimal("120.00"),
            expected_currency="USD",
        )

# ------------------------------------------------- audit round on the client


def test_a_pending_capture_object_is_never_paid_even_when_the_order_says_completed():
    """eCheck captures come back PENDING under an order that reads COMPLETED.
    The capture-object status is the line that keeps that out of the ledger, and
    the earlier version of this test mutated the wrong field."""
    body = json.loads(json.dumps(CAPTURE_OK))
    body["purchase_units"][0]["payments"]["captures"][0]["status"] = "PENDING"
    client, _ = _client(TOKEN_OK, body)

    with pytest.raises(PayPalVerificationError):
        client.capture(
            order_id="5O190127TN364715T",
            expected_amount=Decimal("120.00"),
            expected_currency="USD",
        )


def test_two_capture_objects_on_one_order_are_refused():
    """Money moved twice and one row recorded is the worst outcome available.
    The second capture id would never reach the UNIQUE column."""
    body = json.loads(json.dumps(CAPTURE_OK))
    captures = body["purchase_units"][0]["payments"]["captures"]
    captures.append({**captures[0], "id": "SECONDCAPTURE0001"})
    client, _ = _client(TOKEN_OK, body)

    with pytest.raises(PayPalVerificationError):
        client.capture(
            order_id="5O190127TN364715T",
            expected_amount=Decimal("120.00"),
            expected_currency="USD",
        )


def test_repr_and_traceback_do_not_carry_the_secret():
    """`from None` hides the context from the printer, not from the object. A
    repr or a frame-locals dump must not carry the credential either."""
    import traceback

    client, _ = _client(_status(401))

    with pytest.raises(PayPalRejected) as caught:
        client.create_order(
            amount=Decimal("1.00"),
            currency="USD",
            reference_id="m",
            custom_id="c",
            return_url="https://app.test/r",
            cancel_url="https://app.test/c",
        )

    printed = "".join(traceback.format_exception(caught.value))
    assert "secret-xyz" not in printed
    assert "secret-xyz" not in repr(client)
    assert "secret-xyz" not in repr(CREDS)


def test_a_rejected_bearer_token_is_discarded_so_the_next_call_refetches():
    """A revoked token would otherwise be fingerprinted as valid for hours and
    every call would 401 until the process restarted."""
    client, transport = _client(TOKEN_OK, _status(401), TOKEN_OK, CAPTURE_OK)

    with pytest.raises(PayPalRejected):
        client.capture(
            order_id="5O190127TN364715T",
            expected_amount=Decimal("120.00"),
            expected_currency="USD",
        )
    client.capture(
        order_id="5O190127TN364715T", expected_amount=Decimal("120.00"), expected_currency="USD"
    )

    token_calls = [c for c in transport.calls if c["url"].endswith("/v1/oauth2/token")]
    assert len(token_calls) == 2


def test_an_unavailable_capture_is_marked_as_unknown_outcome():
    """docs/04 §5: a token failure means nothing was attempted (safe to retry);
    a capture timeout means the outcome is unknown (never retry). The caller
    cannot tell those apart from a message string."""
    client, _ = _client(TOKEN_OK, httpx.ConnectError("no route"))

    with pytest.raises(PayPalUnavailable) as caught:
        client.capture(
            order_id="5O190127TN364715T",
            expected_amount=Decimal("120.00"),
            expected_currency="USD",
        )
    assert caught.value.attempted is True

    client2, _ = _client(httpx.ConnectError("no route"))
    with pytest.raises(PayPalUnavailable) as caught2:
        client2.capture(
            order_id="5O190127TN364715T",
            expected_amount=Decimal("120.00"),
            expected_currency="USD",
        )
    assert caught2.value.attempted is False


def test_show_order_is_how_an_unknown_capture_is_resolved():
    """`docs/04 §5` mandates "fetch order status first". Without this method
    that instruction has no code path."""
    client, transport = _client(TOKEN_OK, CAPTURE_OK)

    body = client.show_order("5O190127TN364715T")

    assert body["status"] == "COMPLETED"
    assert transport.calls[-1]["method"] == "GET"
    assert transport.calls[-1]["url"] == (
        "https://api-m.sandbox.paypal.com/v2/checkout/orders/5O190127TN364715T"
    )
