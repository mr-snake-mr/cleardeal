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


def test_the_page_carries_the_unknown_that_matters(client):
    """The A-11 limitation is on screen, not only in a document: the client
    approval in this build is the owner asserting a role."""
    html = client.get(PAGE).text

    assert "A-11" in html
    assert "self-asserted" in html
