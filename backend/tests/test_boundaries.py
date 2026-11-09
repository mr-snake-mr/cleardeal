"""Architectural boundaries, enforced by a test instead of by review.

docs/06 §2 states which layer may import which. A rule that lives only in a
document is a rule that decays: `routers/auth.py` imported `app.models` for a
while, and nothing objected. This file is the objection.

Imports are read from the AST, not from a grep, so a comment mentioning a module
does not fail the build and a conditional import does not slip past.
"""

from __future__ import annotations

import ast
import pathlib
from collections.abc import Iterator

import pytest

APP = pathlib.Path(__file__).resolve().parent.parent / "app"


def _python_files(subpackage: str) -> Iterator[pathlib.Path]:
    directory = APP / subpackage
    if not directory.exists():
        return
    yield from sorted(directory.rglob("*.py"))


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
            # `from app.models import User` records only `app.models`, which is
            # what the boundary is about: the module, not the symbol.
    return modules


def _violations(subpackage: str, forbidden_prefix: str) -> list[str]:
    found: list[str] = []
    for path in _python_files(subpackage):
        for module in _imported_modules(path):
            if module == forbidden_prefix or module.startswith(forbidden_prefix + "."):
                found.append(f"{path.relative_to(APP.parent)} imports {module}")
    return found


@pytest.mark.parametrize(
    ("subpackage", "forbidden"),
    [
        # A route that can see an ORM class can commit one. Services own the
        # transaction, so the router must not be able to reach past them.
        ("routers", "app.models"),
        # A service that imports a router can only be called over HTTP, which
        # makes it untestable from a job or a webhook.
        ("services", "app.routers"),
        # The AI layer must be pure: text in, validated object out. If it can
        # reach the database, it can write, and then the model decides state.
        ("ai", "app.models"),
        ("ai", "app.database"),
    ],
)
def test_layer_boundary_is_respected(subpackage: str, forbidden: str):
    assert _violations(subpackage, forbidden) == []


def test_the_boundary_check_would_catch_a_violation(tmp_path):
    """Negative control.

    Without this, a bug in `_violations` (a wrong prefix, an empty directory)
    makes every test above pass while enforcing nothing.
    """
    fake = tmp_path / "routers"
    fake.mkdir()
    (fake / "leaky.py").write_text("from app.models import User\n", encoding="utf-8")

    tree = ast.parse((fake / "leaky.py").read_text(encoding="utf-8"))
    modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert "app.models" in modules
    assert any(m == "app.models" or m.startswith("app.models.") for m in modules)


def test_every_router_is_mounted_on_the_app():
    """A router file that nobody includes is dead code that looks live.

    Catches the failure where a new router is written, tested at the service
    layer, and never reachable over HTTP.
    """
    from app.main import create_app

    # The OpenAPI document, not `app.routes`: newer FastAPI wraps included
    # routers in an object without `.path`, and the schema is the contract a
    # client actually reads.
    paths = create_app().openapi()["paths"]

    for expected in ("/health", "/api/v1/auth/login", "/api/v1/auth/me", "/api/v1/projects"):
        assert expected in paths, expected
