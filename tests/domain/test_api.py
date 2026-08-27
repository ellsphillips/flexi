"""The public surface of Flexi's dependency-free functional core."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from flexi import domain
from flexi.domain import (
    balance,
    dates,
    formatting,
    leaveyear,
    ledger,
    period,
    punch,
    wallet,
)
from flexi.domain import stitch as stitch_module

LEAF_MODULES = (
    balance,
    dates,
    formatting,
    leaveyear,
    ledger,
    period,
    punch,
    stitch_module,
    wallet,
)


def target_names(target: ast.expr) -> Iterator[str]:
    """Names assigned by one module-level target."""
    if isinstance(target, ast.Name):
        yield target.id
    elif isinstance(target, ast.List | ast.Tuple):
        for item in target.elts:
            yield from target_names(item)


def locally_defined_public_names(module: ModuleType) -> set[str]:
    """Public values defined by a domain leaf rather than imported."""
    path = Path(module.__file__ or "")
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for statement in tree.body:
        if isinstance(statement, ast.AsyncFunctionDef | ast.ClassDef | ast.FunctionDef):
            if not statement.name.startswith("_"):
                found.add(statement.name)
        elif isinstance(statement, ast.TypeAlias):
            found.update(
                name
                for name in target_names(statement.name)
                if not name.startswith("_")
            )
        elif isinstance(statement, ast.AnnAssign | ast.Assign):
            targets = (
                statement.targets
                if isinstance(statement, ast.Assign)
                else [statement.target]
            )
            found.update(
                name
                for target in targets
                for name in target_names(target)
                if not name.startswith("_")
            )
    return found


@pytest.mark.parametrize("module", LEAF_MODULES, ids=lambda module: module.__name__)
def test_every_domain_module_declares_an_immutable_api(module: ModuleType) -> None:
    exports: tuple[str, ...] = module.__all__

    assert isinstance(exports, tuple)
    assert len(exports) == len(set(exports))
    assert all(hasattr(module, name) for name in exports)
    assert set(exports) == locally_defined_public_names(module)

    namespace: dict[str, object] = {}
    exec(f"from {module.__name__} import *", namespace)  # noqa: S102
    assert set(namespace) - {"__builtins__"} == set(exports)


def test_the_domain_facade_resolves_ambiguous_leaf_names() -> None:
    """A flat API must not make two unrelated ``Cell`` or ``ZERO`` values race."""
    assert domain.PunchCell is punch.Cell
    assert domain.CalendarCell is stitch_module.Cell
    assert domain.ZERO_DURATION is balance.ZERO
    assert domain.ZERO_TEXT is formatting.ZERO


def test_imported_implementation_dependencies_are_not_public() -> None:
    """Wildcard consumers get Flexi's API, not the modules used to build it."""
    assert {"Iterable", "dataclass", "datetime", "timedelta"}.isdisjoint(
        balance.__all__
    )
