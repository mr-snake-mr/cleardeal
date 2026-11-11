"""Where the narration key comes from, and in what order.

The key moved from one repo-relative file to three possible sources, so the thing
worth pinning is precedence: an explicit environment variable beats a machine-wide
file, and the machine-wide file beats the checkout. The other half is silence — a
helper that reports the source must never report the secret, or a log becomes a leak.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent  # backend/tests -> cleardeal
NARRATE = REPO / "demo" / "narrate.py"


def where_key(env_extra: dict[str, str]) -> str:
    """Run the helper with a clean-ish environment and return what it names."""
    result = subprocess.run(
        [sys.executable, str(NARRATE), "--where-key"],
        capture_output=True,
        text=True,
        check=False,
        env=env_extra,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def base_env(home: Path) -> dict[str, str]:
    import os

    env = {k: v for k, v in os.environ.items() if k != "GNANI_API_KEY"}
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    return env


def test_the_environment_variable_wins(tmp_path: Path) -> None:
    (tmp_path / ".gnani").mkdir()
    (tmp_path / ".gnani" / "key").write_text("from-the-global-file", encoding="utf-8")
    env = base_env(tmp_path)
    env["GNANI_API_KEY"] = "from-the-environment"
    assert where_key(env) == "env"


def test_the_machine_wide_file_beats_the_checkout(tmp_path: Path) -> None:
    (tmp_path / ".gnani").mkdir()
    (tmp_path / ".gnani" / "key").write_text("from-the-global-file", encoding="utf-8")
    assert where_key(base_env(tmp_path)) == "~/.gnani/key"


def test_a_bare_machine_falls_back_to_the_checkout(tmp_path: Path) -> None:
    # No key anywhere on this pretend machine, so the repo file is the only candidate.
    assert where_key(base_env(tmp_path)) == "demo/.gnani-key"


def test_an_empty_environment_variable_is_not_a_key(tmp_path: Path) -> None:
    (tmp_path / ".gnani").mkdir()
    (tmp_path / ".gnani" / "key").write_text("from-the-global-file", encoding="utf-8")
    env = base_env(tmp_path)
    env["GNANI_API_KEY"] = "   "
    assert where_key(env) == "~/.gnani/key"


def test_reporting_the_source_never_prints_the_secret(tmp_path: Path) -> None:
    (tmp_path / ".gnani").mkdir()
    (tmp_path / ".gnani" / "key").write_text("sk_live_do_not_leak_me", encoding="utf-8")
    env = base_env(tmp_path)
    env["GNANI_API_KEY"] = "sk_live_do_not_leak_me_either"
    result = subprocess.run(
        [sys.executable, str(NARRATE), "--where-key"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert "do_not_leak" not in result.stdout + result.stderr
