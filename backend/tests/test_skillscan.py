"""The sweep aid, tested on a directory small enough to reason about.

`skillscan.py` decides nothing on its own, so its failure mode is quiet: a bad
ranking produces a plausible shortlist and nobody notices the skill that should
have been on it. These tests pin the two things that make the ranking honest —
every skill is counted, and a keyword that is in the description wins.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent  # backend/tests -> cleardeal
TOOL = REPO / "tools" / "skillscan.py"


@pytest.fixture()
def skills(tmp_path: Path) -> Path:
    root = tmp_path / "skills"
    for name, description in (
        ("invoice-export", "Exports invoices to a spreadsheet, with totals."),
        ("poetry-slam", "Writes verse for a stage performance."),
    ):
        (root / name).mkdir(parents=True)
        (root / name / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
            encoding="utf-8",
        )
    return root


def run(skills: Path, task: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, str(TOOL), "--task", task, "--skills-dir", str(skills)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_it_counts_every_skill_it_scanned(skills: Path) -> None:
    result = run(skills, "export invoices")

    assert result.returncode == 0, result.stdout
    assert "scanned: 2 skills" in result.stdout


def test_the_matching_skill_ranks_above_the_unrelated_one(skills: Path) -> None:
    result = run(skills, "export invoices to spreadsheet")

    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()[:1].isdigit()]
    assert lines, result.stdout
    assert "invoice-export" in lines[0], result.stdout


def test_a_task_matching_nothing_still_reports_the_full_count(skills: Path) -> None:
    result = run(skills, "kubernetes")

    assert "matched: 0" in result.stdout
    assert "by description alone: 2" in result.stdout


def test_a_missing_directory_fails(tmp_path: Path) -> None:
    result = run(tmp_path / "absent", "anything")

    assert result.returncode == 1
    assert "no skills directory" in result.stderr
