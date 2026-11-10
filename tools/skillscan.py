"""Sweep all installed skills, and say which ones a task could touch.

`AGENTS.md` requires an exhaustive sweep of 370 skill bodies. Reading 370 files
does not fit in any context window, and pretending otherwise is the dishonesty
§5 of that document refuses. What does fit is every skill's `name` and
`description` — the two fields a skill uses to announce itself — so this reads
those, matches them against the task in words, and prints the shortlist plus the
count it ruled out by description alone.

It is a triage, not a decision: a low score is a reason to look, never a reason
to skip looking. The shortlist is what gets read in full and entered in
`SKILLS-LEDGER.md`.

Usage:
    python tools/skillscan.py --task "invitation client sign page"
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

DEFAULT_SKILLS_DIR = Path(
    r"C:\Users\nagaa\.accio\accounts\7100617678\agents"
    r"\DID-82AD6B-3582AD6BU1794224-4141-FEAC99\agent-core\skills"
)

# Words that appear in almost every description and therefore rank nothing.
STOP = {
    "the", "and", "for", "with", "when", "use", "uses", "using", "any", "this",
    "that", "you", "your", "are", "not", "but", "from", "into", "all", "can",
    "task", "tasks", "work", "working", "code", "file", "files",
}

NAME = re.compile(r"^name:\s*(.+)$", re.MULTILINE)
DESCRIPTION = re.compile(r"^description:\s*(.+?)(?=^\w+:|\Z)", re.MULTILINE | re.DOTALL)


def frontmatter(skill: Path) -> tuple[str, str]:
    text = (skill / "SKILL.md").read_text(encoding="utf-8", errors="replace")[:4000]
    name = NAME.search(text)
    description = DESCRIPTION.search(text)
    body = " ".join(description.group(1).split()) if description else ""
    return (name.group(1).strip() if name else skill.name), body


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in STOP}


def scan(root: Path, task: str) -> list[tuple[int, str, str]]:
    wanted = words(task)
    rows: list[tuple[int, str, str]] = []
    for skill in sorted(p for p in root.iterdir() if p.is_dir()):
        if not (skill / "SKILL.md").is_file():
            continue
        name, description = frontmatter(skill)
        score = len(wanted & words(name + " " + description))
        rows.append((score, skill.name, description))
    return sorted(rows, key=lambda r: (-r[0], r[1]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rank installed skills against a task.")
    parser.add_argument("--task", required=True, help="the task in words, not code")
    parser.add_argument(
        "--skills-dir",
        type=Path,
        default=Path(os.environ.get("SKILLS_DIR", DEFAULT_SKILLS_DIR)),
    )
    parser.add_argument("--top", type=int, default=15)
    args = parser.parse_args(argv)

    if not args.skills_dir.is_dir():
        print(f"no skills directory at {args.skills_dir}", file=sys.stderr)
        return 1

    rows = scan(args.skills_dir, args.task)
    hits = [r for r in rows if r[0] > 0]

    print(f"scanned: {len(rows)} skills   matched: {len(hits)}   by description alone: {len(rows) - len(hits)}")
    for score, name, description in hits[: args.top]:
        print(f"  {score:>2}  {name:<34} {description[:96]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
