"""Record the three-minute ClearDeal demo, following `DEMO.md`.

Written to the `ui-demo` skill's procedure: discover, rehearse, then record.
Python Playwright rather than Node because that is what this venv has, and the
procedure is the same either way â€” cursor overlay, subtitle bar, visible typing,
no silent selector failures.

    python demo/record_demo.py --rehearse   # no video, every selector must pass
    python demo/record_demo.py              # records demo/cleardeal-demo.webm
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

BASE = "http://127.0.0.1:8010"
HERE = Path(__file__).resolve().parent
REHEARSE = "--rehearse" in sys.argv
VIEWPORT = {"width": 1280, "height": 720}

CLIENT_ASK = "Can you also add Google login and throw in the copywriting?"

# Submission rule: under three minutes. Enforced after every take.
LIMIT = 180.0


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


def subtitle(page: Page, text: str, hold: int = 900) -> None:
    page.evaluate(
        """(t) => { const b = document.getElementById('demo-subtitle');
                   if (!b) return; b.textContent = t; b.style.opacity = t ? '1' : '0'; }""",
        text,
    )
    page.wait_for_timeout(hold)


def click(page: Page, selector: str, label: str, pause: int = 1200) -> bool:
    el = page.locator(selector).first
    if el.is_visible() is False:
        print(f"FAIL: {label} not visible ({selector})")
        return False
    el.scroll_into_view_if_needed()
    page.wait_for_timeout(250)
    box = el.bounding_box()
    if box:
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=12)
        page.wait_for_timeout(350)
    el.click()
    page.wait_for_timeout(pause)
    print(f"OK: {label}")
    return True


def wait_text(page: Page, selector: str, needle: str, timeout: int = 90_000) -> str:
    page.wait_for_function(
        """([sel, text]) => {
             const el = document.querySelector(sel);
             return el && el.textContent.includes(text);
           }""",
        arg=[selector, needle],
        timeout=timeout,
    )
    return page.text_content(selector) or ""


def run(page: Page) -> None:
    # Hard rule from the submission brief: the video must be under three
    # minutes. The marks below are the timestamps the narration is placed at,
    # and they are the only way to know whether a take fits without watching it.
    start = time.monotonic()

    def mark(label: str) -> None:
        print(f"MARK {time.monotonic() - start:6.1f}s  {label}")

    page.goto(BASE, wait_until="domcontentloaded")
    inject_overlays(page)
    page.wait_for_timeout(600)

    mark("sign in")
    subtitle(page, "Step 1 - Sign in", 500)
    click(page, "#login", "sign in", pause=600)
    wait_text(page, "#auth-status", "Signed in")

    mark("project")
    subtitle(page, "Step 2 - The brief becomes requirements", 500)
    # A fresh client address per take: the invitation refuses an address that
    # already has an account, by design.
    page.fill("#client-email", f"client-{int(time.time())}@example.test")
    click(page, "#create-project", "create project", pause=600)
    wait_text(page, "#project-status", "created")
    click(page, "#analyse", "analyse brief", pause=300)
    subtitle(page, "Reading the brief...", 600)
    rows = page.locator("#analysis-out tbody tr")
    rows.first.wait_for(timeout=120_000)
    rows.first.scroll_into_view_if_needed()
    page.wait_for_timeout(600)
    subtitle(page, "Every requirement is explicit or inferred", 900)

    mark("draft")
    subtitle(page, "Step 3 - Draft, and the number that matters", 500)
    click(page, "#draft", "draft agreement", pause=500)
    subtitle(page, "Drafting from the accepted requirements...", 600)
    page.wait_for_selector("#agreement-out table", timeout=120_000)
    total = page.text_content(".note.sum") or ""
    print("TOTAL LINE:", " ".join(total.split()))
    page.evaluate("document.querySelector('.note.sum').scrollIntoView({block:'center'})")
    subtitle(page, "Milestones must equal the budget to the cent", 1300)

    mark("signature one")
    subtitle(page, "Step 4 - Send, sign, then check the claim", 500)
    click(page, "#send", "send", pause=500)
    click(page, "#approve-freelancer", "approve as freelancer", pause=700)

    mark("scope check")
    page.evaluate("document.getElementById('change-section').scrollIntoView({block:'start'})")
    page.wait_for_timeout(400)
    page.locator("#change-text").press_sequentially(CLIENT_ASK, delay=8)
    click(page, "#check", "check against scope", pause=300)
    page.wait_for_selector("#change-out table", timeout=120_000)
    page.wait_for_timeout(400)
    subtitle(page, "OUT_OF_SCOPE, with the sentence that proves it", 1500)
    page.evaluate("document.getElementById('change-out').scrollIntoView({block:'center'})")
    page.wait_for_timeout(1200)

    mark("invite")
    subtitle(page, "Step 5 - The second signature needs a second person", 500)
    click(page, "#invite", "invite the client", pause=500)
    page.evaluate("document.getElementById('invite-out').scrollIntoView({block:'center'})")
    subtitle(page, "The link carries the client's own session", 900)

    mark("client view")
    click(page, "a.quiet[href*='/invite/']", "sign as client in this tab", pause=400)
    page.wait_for_load_state("load")
    inject_overlays(page)
    page.wait_for_timeout(700)
    subtitle(page, "This tab is now the client, not a second role", 900)

    mark("signature two")
    click(page, "#approve-client", "sign as client", pause=700)
    wait_text(page, "#agreement-status", "APPROVED")
    subtitle(page, "Both signatures in: APPROVED", 1800)
    mark("end")
    subtitle(page, "")


def duration(path: Path) -> float:
    """Seconds of video, read from the container rather than guessed."""
    import subprocess

    out = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        return -1.0


def main() -> int:
    failed = False
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            viewport=VIEWPORT,
            record_video_dir=None if REHEARSE else str(HERE / "raw"),
            record_video_size=VIEWPORT,
        )
        page = context.new_page()
        page.on("console", lambda m: print("  console:", m.text) if m.type == "error" else None)
        try:
            run(page)
            print("REHEARSAL PASSED" if REHEARSE else "FLOW COMPLETED")
        except Exception as exc:  # noqa: BLE001 - a failed demo must say where
            page.screenshot(path=str(HERE / "failure.png"), full_page=True)
            print(f"DEMO FAILED: {type(exc).__name__}: {str(exc)[:300]}")
            return 1
        finally:
            video = page.video
            context.close()
            if not REHEARSE and video is not None:
                target = HERE / "cleardeal-demo.webm"
                Path(video.path()).replace(target)
                seconds = duration(target)
                print(f"VIDEO: {target}  {seconds:.1f}s")
                if seconds >= LIMIT:
                    # The brief's rule, enforced here rather than remembered at
                    # upload time: a four-minute take is a failed take.
                    print(f"FAIL: {seconds:.1f}s is not under {LIMIT:.0f}s")
                    failed = True
            browser.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

