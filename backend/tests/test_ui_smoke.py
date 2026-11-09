"""One real browser, one real server, one real cookie.

The other frontend tests assert that the HTML contains the right strings. A page
whose JavaScript throws on the first line would pass all of them. This drives
the page instead: it signs in, creates a project, and reads the status lines the
page itself writes — which only appear if `api()`, the cookie, the error envelope
and the click handlers all work.

It skips when Playwright or its browser is absent. A skip is honest; a green
test that never opened a page is not.

Deliberately out of scope: anything past project creation. Analysis and drafting
call a provider, and this test must never depend on a key or a network.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

playwright_api = pytest.importorskip(
    "playwright.sync_api", reason="playwright is not installed"
)

BACKEND = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def live_server(tmp_path_factory) -> Iterator[str]:
    """A real uvicorn process, on its own database, with DEBUG on.

    DEBUG matters for one reason: session cookies are `Secure` outside debug, and
    a browser silently drops a `Secure` cookie over `http://`. That is not a bug
    in the app — it is the app being right — so the test runs the way a developer
    runs it locally.
    """
    port = _free_port()
    workspace = tmp_path_factory.mktemp("ui")
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{workspace / 'ui.db'}",
        "DEBUG": "true",
        "SESSION_SECRET": "ui-test-secret-2f8c1d4e6a9b0c3f5e7d1a2b4c6d8e0f",
        "CREDENTIALS_ENCRYPTION_KEY": (
            "6f1c2b7d4e8a90c3f5d6b7a1e2c4d8f0a3b5c7d9e1f2a4b6c8d0e2f4a6b8c0d2"
        ),
    }

    subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=BACKEND,
        env=env,
        check=True,
        capture_output=True,
    )

    process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=BACKEND,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(60):
            try:
                if httpx.get(f"{base}/health", timeout=0.5).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.25)
        else:
            pytest.fail("the server never became ready")
        yield base
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            process.kill()


@pytest.fixture(scope="module")
def browser():
    try:
        with playwright_api.sync_playwright() as playwright:
            launched = playwright.chromium.launch()
            yield launched
            launched.close()
    except Exception as exc:  # noqa: BLE001 - a missing browser is a skip
        pytest.skip(f"chromium is unavailable: {exc}")


def test_the_page_actually_works(live_server, browser):
    page = browser.new_page()
    try:
        page.goto(live_server, wait_until="domcontentloaded")
        assert page.title() == "ClearDeal — scope, checked"

        page.fill("#email", "browser@example.test")
        page.fill("#password", "correct horse battery staple")
        page.click("#login")
        page.wait_for_function(
            "document.getElementById('auth-status').textContent.includes('Signed in')",
            timeout=15_000,
        )

        # The session cookie survived the round trip, because this next call is
        # authenticated and the page reports its own failure otherwise.
        page.click("#create-project")
        page.wait_for_function(
            "document.getElementById('project-status').textContent.includes('created')",
            timeout=15_000,
        )

        # A provider-free interaction that still proves the fetch wrapper: the
        # server refuses, and the page must render the refusal as a sentence.
        page.click("#analyse")
        page.wait_for_function(
            "document.getElementById('analyse-status').textContent.length > 0",
            timeout=15_000,
        )
        status = page.text_content("#analyse-status")
        assert status.strip(), "the page showed nothing after a failed analysis"
    finally:
        page.close()
