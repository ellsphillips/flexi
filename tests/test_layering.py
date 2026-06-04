"""The layering rule, enforced.

``flexi.domain`` may not import Textual or SQLAlchemy, and ``flexi.components``
may not import SQLAlchemy. Both rules are what keep the arithmetic testable
without a terminal and the widgets testable without a database, and both are the
kind of rule that decays silently the first time someone needs one import "just
here". Twenty lines of AST walking is cheaper than the decay.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "flexi"

FORBIDDEN: dict[str, frozenset[str]] = {
    "domain": frozenset({"textual", "sqlalchemy", "flexi.services", "flexi.models"}),
    "components": frozenset({"sqlalchemy", "flexi.models"}),
    "screens": frozenset({"sqlalchemy"}),
}


def imported_modules(source: Path) -> Iterator[str]:
    """Every module name the file imports, dotted and absolute."""
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            yield node.module


def python_files(package: str) -> list[Path]:
    return sorted((SRC / package).rglob("*.py"))


@pytest.mark.parametrize("package", sorted(FORBIDDEN))
def test_package_exists(package: str) -> None:
    """It fails loudly if a package is renamed and the rule is left behind."""
    assert (SRC / package).is_dir(), f"flexi/{package}/ is missing"


@pytest.mark.parametrize(
    ("package", "path"),
    [
        (package, path)
        for package in sorted(FORBIDDEN)
        if (SRC / package).is_dir()
        for path in python_files(package)
    ],
    ids=lambda value: value.name if isinstance(value, Path) else str(value),
)
def test_layer_imports(package: str, path: Path) -> None:
    """It keeps each layer inside the imports it is allowed."""
    banned = FORBIDDEN[package]
    for module in imported_modules(path):
        root = module.split(".")[0]
        offending = next(
            (rule for rule in banned if root == rule or module.startswith(f"{rule}.")),
            None,
        )
        assert offending is None, (
            f"{path.relative_to(SRC)} imports {module!r}; "
            f"flexi/{package}/ may not depend on {offending!r}"
        )
