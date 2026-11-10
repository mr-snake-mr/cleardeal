"""The skill rule, as something that can fail.

`AGENTS.md` says nothing is written without sweeping the catalogue, activating
the skills that apply, and declaring both sets before the work starts. A rule
that lives only in prose is a rule nobody can fail, so this makes it checkable:
it reads `SKILLS-LEDGER.md` and refuses a ledger that is decorative.

It cannot know whether a skill was read. It can know that the ledger is complete
enough to have required a real sweep:

  * at least one skill applied, and at least five ruled out — a sweep that rules
    out nothing did not happen;
  * every named skill exists on disk, so a misspelt or invented name fails;
  * every reason is long enough to mean something, and not a placeholder;
  * no skill is on both lists.

Exit 0 prints PASS. Anything else exits 1 and lists every failure, because a
gate that stops at the first problem makes the next run a guess.

Usage:
    python tools/skillgate.py [--ledger PATH] [--skills-dir PATH]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Where the 370 skill bodies live on this machine. The catalogue records the
# same absolute path; the rule is about this workspace, so it is a default and
# not a magic constant. `--skills-dir` and `SKILLS_DIR` both override it.
DEFAULT_SKILLS_DIR = Path(
    r"C:\Users\nagaa\.accio\accounts\7100617678\agents"
    r"\DID-82AD6B-3582AD6BU1794224-4141-FEAC99\agent-core\skills"
)

MIN_APPLIED = 1
MIN_RULED_OUT = 5
MIN_REASON = 20

PLACEHOLDERS = {
    "",
    "-",
    "n/a",
    "na",
    "none",
    "nothing",
    "tbd",
    "todo",
    "overkill",
    "not applicable",
    "not needed",
    "irrelevant",
}

ROW = re.compile(r"^\|\s*`?([A-Za-z0-9._/-]+)`?\s*\|\s*(.*?)\s*\|\s*$")
HEADING = re.compile(r"^##\s+(.*)$")


def parse_ledger(text: str) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Return (applied, ruled_out) as (skill, reason) pairs.

    Only the two named sections are read. Anything else in the file is prose and
    is ignored, so the ledger can carry an explanation above the tables without
    becoming a parsing problem.
    """
    applied: list[tuple[str, str]] = []
    ruled_out: list[tuple[str, str]] = []
    section: list[tuple[str, str]] | None = None

    for line in text.splitlines():
        heading = HEADING.match(line.strip())
        if heading:
            name = heading.group(1).strip().lower()
            section = applied if name.startswith("applied") else None
            if name.startswith("ruled out"):
                section = ruled_out
            continue
        if section is None:
            continue

        match = ROW.match(line.strip())
        if match is None:
            continue
        skill, reason = match.group(1), match.group(2)
        if skill.lower() in {"skill", "id"} or set(reason) <= {"-", ":", " "}:
            continue  # header row or separator row
        section.append((skill, reason))

    return applied, ruled_out


def check(ledger: Path, skills_dir: Path) -> list[str]:
    problems: list[str] = []

    if not ledger.is_file():
        return [f"no ledger at {ledger} — the rule was not declared"]

    applied, ruled_out = parse_ledger(ledger.read_text(encoding="utf-8"))

    if len(applied) < MIN_APPLIED:
        problems.append(
            f"{ledger.name}: no skill applied. The rule says read one and work inside it."
        )
    if len(ruled_out) < MIN_RULED_OUT:
        problems.append(
            f"{ledger.name}: {len(ruled_out)} skills ruled out, {MIN_RULED_OUT} required. "
            "A sweep that rules out nothing is not a sweep."
        )

    if not skills_dir.is_dir():
        problems.append(f"skills directory not found: {skills_dir}")
        return problems

    installed = {p.name for p in skills_dir.iterdir() if p.is_dir()}

    for label, rows in (("applied", applied), ("ruled out", ruled_out)):
        for skill, reason in rows:
            if skill not in installed:
                problems.append(f"{label}: '{skill}' is not an installed skill")
            cleaned = reason.strip().strip("*").strip()
            if cleaned.lower() in PLACEHOLDERS:
                problems.append(f"{label}: '{skill}' has a placeholder reason: {reason!r}")
            elif len(cleaned) < MIN_REASON:
                problems.append(
                    f"{label}: '{skill}' reason is {len(cleaned)} characters, "
                    f"{MIN_REASON} required — say what it was ruled out for"
                )

    both = {s for s, _ in applied} & {s for s, _ in ruled_out}
    for skill in sorted(both):
        problems.append(f"'{skill}' is both applied and ruled out")

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the skill ledger before a write.")
    parser.add_argument("--ledger", type=Path, default=REPO / "SKILLS-LEDGER.md")
    parser.add_argument(
        "--skills-dir",
        type=Path,
        default=Path(os.environ.get("SKILLS_DIR", DEFAULT_SKILLS_DIR)),
    )
    args = parser.parse_args(argv)

    problems = check(args.ledger, args.skills_dir)

    if problems:
        print("SKILL GATE: FAIL")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    applied, ruled_out = parse_ledger(args.ledger.read_text(encoding="utf-8"))
    print(f"SKILL GATE: PASS ({len(applied)} applied, {len(ruled_out)} ruled out)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
