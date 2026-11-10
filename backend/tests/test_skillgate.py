"""The gate has to be able to fail, or it is decoration.

Each test builds a throwaway skills directory and a throwaway ledger, so nothing
here depends on which skills happen to be installed on the machine running it —
except the last test, which checks the ledger that ships with the repo.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent  # backend/tests -> cleardeal
TOOL = REPO / "tools" / "skillgate.py"

APPLIED = "## Applied\n\n| Skill | Why |\n|---|---|\n"
RULED = "\n## Ruled out\n\n| Skill | Why |\n|---|---|\n"

GOOD_REASON = "read it and it governed the change, which is the point of the rule"


@pytest.fixture()
def skills(tmp_path: Path) -> Path:
    root = tmp_path / "skills"
    for name in ("alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta"):
        (root / name).mkdir(parents=True)
    return root


def run(ledger: Path, skills: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, str(TOOL), "--ledger", str(ledger), "--skills-dir", str(skills)],
        capture_output=True,
        text=True,
        check=False,
    )


def write(tmp_path: Path, body: str) -> Path:
    ledger = tmp_path / "ledger.md"
    ledger.write_text(body, encoding="utf-8")
    return ledger


def ruled_block(count: int = 5, reason: str = GOOD_REASON) -> str:
    names = ["beta", "gamma", "delta", "epsilon", "zeta", "eta"]
    rows = "".join(f"| `{n}` | {reason} |\n" for n in names[:count])
    return RULED + rows


def test_a_ledger_that_declares_nothing_fails(tmp_path: Path, skills: Path) -> None:
    result = run(write(tmp_path, APPLIED + ruled_block()), skills)

    assert result.returncode == 1
    assert "no skill applied" in result.stdout


def test_a_sweep_that_rules_out_too_little_fails(tmp_path: Path, skills: Path) -> None:
    ledger = write(tmp_path, APPLIED + "| `alpha` | " + GOOD_REASON + " |\n" + ruled_block(2))

    result = run(ledger, skills)

    assert result.returncode == 1
    assert "2 skills ruled out" in result.stdout


def test_a_skill_that_is_not_installed_fails(tmp_path: Path, skills: Path) -> None:
    ledger = write(
        tmp_path,
        APPLIED + "| `invented-skill` | " + GOOD_REASON + " |\n" + ruled_block(),
    )

    result = run(ledger, skills)

    assert result.returncode == 1
    assert "invented-skill" in result.stdout
    assert "not an installed skill" in result.stdout


def test_a_placeholder_reason_fails(tmp_path: Path, skills: Path) -> None:
    ledger = write(
        tmp_path,
        APPLIED + "| `alpha` | overkill |\n" + ruled_block(),
    )

    result = run(ledger, skills)

    assert result.returncode == 1
    assert "placeholder reason" in result.stdout


def test_a_skill_on_both_lists_fails(tmp_path: Path, skills: Path) -> None:
    ledger = write(
        tmp_path,
        APPLIED + "| `beta` | " + GOOD_REASON + " |\n" + ruled_block(),
    )

    result = run(ledger, skills)

    assert result.returncode == 1
    assert "both applied and ruled out" in result.stdout


def test_a_missing_ledger_fails(tmp_path: Path, skills: Path) -> None:
    result = run(tmp_path / "absent.md", skills)

    assert result.returncode == 1
    assert "no ledger" in result.stdout


def test_a_complete_ledger_passes(tmp_path: Path, skills: Path) -> None:
    ledger = write(tmp_path, APPLIED + "| `alpha` | " + GOOD_REASON + " |\n" + ruled_block())

    result = run(ledger, skills)

    assert result.returncode == 0, result.stdout
    assert "PASS (1 applied, 5 ruled out)" in result.stdout
