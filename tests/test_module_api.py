"""The explicit API of Flexi's top-level modules."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path
from types import MappingProxyType, ModuleType
from typing import cast

import pytest
from textual.app import App as TextualApp
from textual.screen import Screen

import flexi.__main__ as entrypoint
from flexi import (
    app,
    config,
    constants,
    context,
    locations,
    messages,
    provider,
    versioning,
    wallclock,
)
from flexi.services.registry import Services
from tests.public_api import contains_any, public_type_hints

MODULES = (
    app,
    config,
    constants,
    context,
    entrypoint,
    locations,
    messages,
    provider,
    versioning,
    wallclock,
)


class ServiceOnlyApp(TextualApp[None]):
    """A valid module host that deliberately has no command operations."""

    services = cast("Services", object())


def module_statements(statements: list[ast.stmt]) -> Iterator[ast.stmt]:
    """Statements evaluated at module scope, including conditional branches."""
    for statement in statements:
        yield statement
        if isinstance(statement, ast.If):
            yield from module_statements(statement.body)
            yield from module_statements(statement.orelse)


def target_names(target: ast.expr) -> Iterator[str]:
    if isinstance(target, ast.Name):
        yield target.id
    elif isinstance(target, ast.List | ast.Tuple):
        for item in target.elts:
            yield from target_names(item)


def locally_defined_public_names(module: ModuleType) -> set[str]:
    """Public values defined by a module rather than imported into it."""
    path = Path(module.__file__ or "")
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for statement in module_statements(tree.body):
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


def wildcard_names(module: ModuleType) -> set[str]:
    namespace: dict[str, object] = {}
    exec(f"from {module.__name__} import *", namespace)  # noqa: S102
    return set(namespace) - {"__builtins__"}


@pytest.mark.parametrize("module", MODULES, ids=lambda module: module.__name__)
def test_each_top_level_module_publishes_every_local_name(module: ModuleType) -> None:
    assert isinstance(module.__all__, tuple)
    assert len(module.__all__) == len(set(module.__all__))
    assert set(module.__all__) == locally_defined_public_names(module)
    assert wildcard_names(module) == set(module.__all__)


@pytest.mark.parametrize("module", MODULES, ids=lambda module: module.__name__)
def test_public_annotations_are_resolvable(module: ModuleType) -> None:
    checked = list(public_type_hints(module))
    assert checked
    for qualified, hints in checked:
        assert hints, f"{qualified} has no annotations"
        assert not any(map(contains_any, hints.values())), qualified


def test_closed_constant_tables_and_choices_are_immutable() -> None:
    for name in ("_DETAILS", "_DIVISION_LABELS", "_PORTION_LABELS", "_SPOKEN"):
        assert isinstance(getattr(constants, name), MappingProxyType)
    assert isinstance(constants.Division.choices(), tuple)


def test_context_adapters_reject_objects_without_the_required_structure() -> None:
    """A misplaced widget fails at the typed boundary, not at a later attribute."""
    with pytest.raises(TypeError, match="module period and time context"):
        context.module_host(Screen())
    with pytest.raises(TypeError, match="Flexi service context"):
        context.service_app(TextualApp())
    with pytest.raises(TypeError, match="Flexi command context"):
        context.command_app(TextualApp())
    with pytest.raises(TypeError, match="complete Flexi application context"):
        context.flexi_app(TextualApp())


def test_context_adapters_apply_interface_segregation() -> None:
    """A service host need not pretend to implement unrelated app actions."""
    app = ServiceOnlyApp()

    assert context.service_app(app) is app
    with pytest.raises(TypeError, match="Flexi command context"):
        context.command_app(app)
    with pytest.raises(TypeError, match="complete Flexi application context"):
        context.flexi_app(app)
