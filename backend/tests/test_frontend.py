"""The page is part of the contract.

These are not UI tests — there is no browser here. They assert the three things
that silently rot a one-page demo: it stops being served, it starts depending on
a CDN, or it calls a path the API no longer has.
"""

from __future__ import annotations

PAGE = "/"


def test_the_page_is_served(client):
    response = client.get(PAGE)

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "ClearDeal" in response.text


def test_the_page_runs_with_no_network_beyond_this_app(client):
    """A demo on someone else's wifi must not depend on a CDN being reachable."""
    html = client.get(PAGE).text

    assert "http://" not in html
    assert "https://cdn" not in html
    assert "unpkg" not in html and "jsdelivr" not in html


def test_every_endpoint_the_page_calls_still_exists(client):
    """The page and the API drift apart silently otherwise: a renamed route
    leaves a button that fails only when someone clicks it on stage."""
    html = client.get(PAGE).text

    for path in (
        "/auth/register",
        "/auth/login",
        "/projects",
        "/analyse",
        "/agreement",
        "/merchants/paypal",
        "/milestones/",
        "/changes",
        "/send",
        "/approve",
    ):
        assert path in html, path


def test_the_page_requires_two_principals(client):
    """A-15 is closed, so the page must offer the invitation and must not imply
    that the same session can sign both sides."""
    html = client.get(PAGE).text

    assert "/invitations" in html
    assert "Invite the client" in html
    assert "its own session" in html


def test_the_page_captures_the_order_it_created(client):
    """Creating an order is half a payment.

    Without a capture the page can reach APPROVED and never PAID — and the
    webhook cannot rescue it, because PayPal only emits
    PAYMENT.CAPTURE.COMPLETED once a capture exists. Found on the deployed host
    on 2026-11-10: the page printed an approve link and stopped there.
    """
    html = client.get(PAGE).text

    assert "/capture" in html, "the page can create an order but never capture it"
    assert "capturePayment" in html
