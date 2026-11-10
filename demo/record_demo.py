"""Record the ClearDeal walkthrough, following `DEMO.md` and the `ui-demo` method.

Two browser contexts, because the product needs two principals:

  page1  the freelancer - signs in, types the brief, drafts, signs, invites
  page2  the client - arrives from the invitation link in its own session

They are recorded separately and cut together by `assemble.py`. A second *tab*
would share page1's cookie jar and sign the freelancer out when the link is
followed, which is why this is a context and not a tab.

    python demo/record_demo.py --rehearse   # no video; every selector must pass
    python demo/record_demo.py              # writes demo/out/*.webm + marks.json
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, sync_playwright

BASE = "http://127.0.0.1:8010"
HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
REHEARSE = "--rehearse" in sys.argv
VIEWPORT = {"width": 1280, "height": 720}

BRIEF = (
    "Five page marketing site with a blog index and a contact form. "
    "Budget is 300 dollars. Copywriting and photography are not included."
)
CLIENT_ASK = "Can you also add Google login and throw in the copywriting?"


def paypal_credentials() -> tuple[str, str]:
    """Read the sandbox pair from `backend/.env`, which is gitignored.

    Typing them on camera is the point of the beat; keeping them out of this
    file is the point of this function. Never print them.
    """
    env: dict[str, str] = {}
    for line in (HERE.parent / "backend/.env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    return env.get("PAYPAL_CLIENT_ID", ""), env.get("PAYPAL_CLIENT_SECRET", "")


def inject_overlays(page: Page) -> None:
    page.evaluate(
        """() => {
      if (document.getElementById('demo-cursor')) return;
      const c = document.createElement('div');
      c.id = 'demo-cursor';
      c.innerHTML = `<svg width="22" height="22" viewBox="0 0 24 24" fill="none">
        <path d="M5 3L19 12L12 13L9 20L5 3Z" fill="white" stroke="black"
              stroke-width="1.5" stroke-linejoin="round"/></svg>`;
      c.style.cssText = `position:fixed; z-index:2147483647; pointer-events:none;
        width:22px; height:22px; transition:left .08s linear, top .08s linear;
        filter: drop-shadow(1px 1px 2px rgba(0,0,0,.35));`;
      document.body.appendChild(c);
      document.addEventListener('mousemove', (e) => {
        c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px';
      });

      const b = document.createElement('div');
      b.id = 'demo-subtitle';
      b.style.cssText = `position:fixed; bottom:0; left:0; right:0; z-index:2147483646;
        text-align:center; padding:14px 24px; background:rgba(10,14,20,.82);
        color:#fff; font-family:'Segoe UI',system-ui,sans-serif; font-size:17px;
        letter-spacing:.2px; opacity:0; transition:opacity .3s; pointer-events:none;`;
      document.body.appendChild(b);
    }"""
    )


def subtitle(page: Page, text: str, hold: int = 700) -> None:
    page.evaluate(
        """(t) => { const b = document.getElementById('demo-subtitle');
                   if (!b) return; b.textContent = t; b.style.opacity = t ? '1' : '0'; }""",
        text,
    )
    page.wait_for_timeout(hold)


def click(page: Page, selector: str, label: str, pause: int = 700) -> None:
    el = page.locator(selector).first
    if not el.is_visible():
        raise AssertionError(f"{label} is not visible ({selector})")
    el.scroll_into_view_if_needed()
    page.wait_for_timeout(200)
    box = el.bounding_box()
    if box:
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=10)
        page.wait_for_timeout(250)
    el.click()
    page.wait_for_timeout(pause)
    print(f"OK: {label}")


def type_into(page: Page, selector: str, text: str, label: str, delay: int = 18) -> None:
    """Clear the field and type, so the viewer sees the value being entered.

    A prefilled form looks like a magic trick. Typed values look like work, and
    a viewer can follow work.
    """
    el = page.locator(selector).first
    el.scroll_into_view_if_needed()
    el.click()
    page.wait_for_timeout(150)
    el.fill("")
    el.press_sequentially(text, delay=delay)
    page.wait_for_timeout(250)
    print(f"TYPED: {label}")


def wait_text(page: Page, selector: str, needle: str, timeout: int = 120_000) -> None:
    page.wait_for_function(
        """([sel, text]) => {
             const el = document.querySelector(sel);
             return el && el.textContent.includes(text);
           }""",
        arg=[selector, needle],
        timeout=timeout,
    )


def wait_or_refusal(page: Page, selector: str, status_id: str, timeout: int = 150_000) -> None:
    """Wait for the result, and stop the moment the server refuses.

    A refused draft leaves no table behind, so a plain wait burns the whole
    timeout and reports nothing. The refusal is a sentence on the page, and it
    is the only useful thing to say about a failed take.
    """
    page.wait_for_function(
        """([sel, sid]) => {
             const el = document.querySelector(sel);
             if (el && el.offsetParent !== null) return true;
             const st = document.getElementById(sid);
             return !!(st && /discard|unavailable|rejected|invalid|was not changed|could not run/i
                       .test(st.textContent));
           }""",
        arg=[selector, status_id],
        timeout=timeout,
    )
    if not page.locator(selector).first.is_visible():
        raise AssertionError(f"refused: {(page.text_content('#' + status_id) or '').strip()}")


def run(context: BrowserContext, client_context: BrowserContext) -> dict[str, dict[str, float]]:
    marks: dict[str, dict[str, float]] = {"page1": {}, "page2": {}}
    clock = {"page1": time.monotonic(), "page2": 0.0}

    def mark(page_name: str, label: str) -> None:
        if page_name == "page2" and not clock["page2"]:
            clock["page2"] = time.monotonic()
        seconds = time.monotonic() - clock[page_name]
        marks[page_name][label] = round(seconds, 1)
        print(f"MARK {page_name} {seconds:6.1f}s  {label}")

    page = context.new_page()
    page.goto(BASE, wait_until="domcontentloaded")
    inject_overlays(page)
    page.wait_for_timeout(500)

    mark("page1", "start")
    subtitle(page, "Step 1 - Sign in as the freelancer", 400)
    type_into(page, "#email", "demo@cleardeal.test", "email")
    type_into(page, "#password", "correct horse battery staple", "password")
    click(page, "#login", "sign in", pause=500)
    wait_text(page, "#auth-status", "Signed in")
    subtitle(page, "Next: the brief becomes a table of requirements", 700)

    mark("page1", "project")
    subtitle(page, "Step 2 - Type the project and the brief", 400)
    type_into(page, "#project-name", "Marketing site", "project name")
    type_into(page, "#client-email", f"client-{int(time.time())}@example.test", "client email")
    type_into(page, "#budget", "300.00", "budget")
    type_into(page, "#brief", BRIEF, "brief", delay=9)
    click(page, "#create-project", "create project", pause=400)
    wait_text(page, "#project-status", "created")
    subtitle(page, "Expect: every requirement marked explicit or inferred", 600)
    click(page, "#analyse", "analyse brief", pause=200)
    subtitle(page, "Reading the brief...", 400)
    wait_or_refusal(page, "#analysis-out tbody tr", "analyse-status")
    page.locator("#analysis-out").scroll_into_view_if_needed()
    page.wait_for_timeout(400)
    subtitle(page, "An explicit row quotes the brief; an inferred one admits a guess", 900)

    mark("page1", "draft")
    subtitle(page, "Step 3 - Draft the agreement", 400)
    click(page, "#draft", "draft agreement", pause=400)
    subtitle(page, "Expect: milestones that add up to the budget, to the cent", 400)
    wait_or_refusal(page, "#agreement-out table", "agreement-status")
    total = (page.text_content(".note.sum") or "").split()
    print("TOTAL LINE:", " ".join(total))
    page.evaluate("document.querySelector('.note.sum').scrollIntoView({block:'center'})")
    subtitle(page, "It totals $300.00 against a $300.00 budget: equal, or discarded", 1100)

    mark("page1", "signature one")
    subtitle(page, "Step 4 - One signature, from the freelancer", 400)
    click(page, "#send", "send", pause=400)
    click(page, "#approve-freelancer", "approve as freelancer", pause=500)
    subtitle(page, "Expect: the status now waits for the client", 600)

    mark("page1", "invite")
    subtitle(page, "Step 5 - The second signature needs a second person", 400)
    click(page, "#invite", "invite the client", pause=400)
    page.evaluate("document.getElementById('invite-out').scrollIntoView({block:'center'})")
    subtitle(page, "Expect: a single-use link bound to the client's own account", 900)

    link = page.locator("a.quiet[href*='/invite/']").first.get_attribute("href")
    page2 = client_context.new_page()
    clock["page2"] = time.monotonic()
    page2.goto(link)
    page2.wait_for_load_state("load")
    inject_overlays(page2)
    page2.wait_for_timeout(600)
    mark("page2", "client view")
    subtitle(page2, "The client's page: one agreement, one button", 700)
    click(page2, "#approve-client", "sign as client", pause=500)
    wait_text(page2, "#agreement-status", "APPROVED")
    subtitle(page2, "Expect: both signatures in, APPROVED", 1100)
    mark("page2", "approved")

    page.bring_to_front()
    page.wait_for_timeout(300)
    mark("page1", "scope check")
    subtitle(page, "Step 6 - Ask for a change, expect a verdict", 400)
    page.evaluate("document.getElementById('change-section').scrollIntoView({block:'start'})")
    page.wait_for_timeout(300)
    page.fill("#change-text", "")
    page.locator("#change-text").press_sequentially(CLIENT_ASK, delay=6)
    click(page, "#check", "check against scope", pause=200)
    wait_or_refusal(page, "#change-out table", "change-status")
    page.wait_for_timeout(300)
    subtitle(page, "OUT_OF_SCOPE, with the sentence that proves it", 1200)
    page.evaluate("document.getElementById('change-out').scrollIntoView({block:'center'})")
    page.wait_for_timeout(1000)
    subtitle(page, "Every verdict cites a section, or says it cannot", 900)

    mark("page1", "payment")
    subtitle(page, "Step 7 - Connect the merchant, then pay a milestone", 400)
    page.evaluate("document.getElementById('pay-section').scrollIntoView({block:'start'})")
    page.wait_for_timeout(300)
    client_id, client_secret = paypal_credentials()
    type_into(page, "#pp-id", client_id, "paypal client id", delay=4)
    type_into(page, "#pp-secret", client_secret, "paypal client secret", delay=4)
    click(page, "#connect", "connect merchant", pause=300)
    wait_text(page, "#pay-status", "Merchant connected")
    subtitle(page, "Stored encrypted; the form can never read the secret back", 900)
    click(page, "#pay-out button[data-milestone]", "create order", pause=400)
    wait_text(page, "#pay-status", "Order created")
    subtitle(page, "Order created: the buyer approves in PayPal, then the server captures", 1500)
    mark("page1", "end")
    subtitle(page, "")
    return marks


def main() -> int:
    for folder in ("freelancer", "client"):
        (OUT / folder).mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            viewport=VIEWPORT,
            record_video_dir=None if REHEARSE else str(OUT / "freelancer"),
            record_video_size=VIEWPORT,
        )
        client_context = browser.new_context(
            viewport=VIEWPORT,
            record_video_dir=None if REHEARSE else str(OUT / "client"),
            record_video_size=VIEWPORT,
        )
        marks: dict[str, dict[str, float]] = {}
        try:
            marks = run(context, client_context)
            print("REHEARSAL PASSED" if REHEARSE else "FLOW COMPLETED")
        except Exception as exc:  # noqa: BLE001 - a failed demo must say where
            if context.pages:
                context.pages[0].screenshot(path=str(HERE / "failure.png"), full_page=True)
            print(f"DEMO FAILED: {type(exc).__name__}: {str(exc)[:300]}")
            return 1
        finally:
            videos: dict[str, Path] = {}
            for name, ctx in (("page1", context), ("page2", client_context)):
                for open_page in ctx.pages:
                    if open_page.video is not None:
                        videos[name] = Path(open_page.video.path())
            context.close()
            client_context.close()
            if not REHEARSE and marks:
                for name, source in videos.items():
                    source.replace(OUT / f"{name}.webm")
                (OUT / "marks.json").write_text(json.dumps(marks, indent=2), encoding="utf-8")
                print("VIDEOS:", {name: f"{name}.webm" for name in videos})
                print("MARKS: demo/out/marks.json")
            browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
