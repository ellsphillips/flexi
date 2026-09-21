"""The explicit API of Flexi's top-level modules."""

from __future__ import annotations

import ast
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
from tests.public_api import check_declared_api, check_public_annotations

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
    """A valid module host with no command operations."""

    services = cast("Services", object())


@pytest.mark.parametrize("module", MODULES, ids=lambda module: module.__name__)
def test_top_level_modules_publish_local_names(module: ModuleType) -> None:
    check_declared_api(module)


@pytest.mark.parametrize("module", MODULES, ids=lambda module: module.__name__)
def test_public_annotations_are_resolvable(module: ModuleType) -> None:
    check_public_annotations(module)


def test_closed_constant_tables_and_choices_are_immutable() -> None:
    for name in ("_DETAILS", "_DIVISION_LABELS", "_PORTION_LABELS", "_SPOKEN"):
        assert isinstance(getattr(constants, name), MappingProxyType)
    assert isinstance(constants.Division.choices(), tuple)


def test_context_adapters_reject_the_wrong_object() -> None:
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
    """A service host need not implement unrelated app actions."""
    app = ServiceOnlyApp()

    assert context.service_app(app) is app
    with pytest.raises(TypeError, match="Flexi command context"):
        context.command_app(app)
    with pytest.raises(TypeError, match="complete Flexi application context"):
        context.flexi_app(app)


LAZY_FACADES = (
    "flexi/services/__init__.py",
    "flexi/components/__init__.py",
    "flexi/components/modules/__init__.py",
    "flexi/screens/__init__.py",
    "flexi/cli/__init__.py",
    "flexi/cli/ui/__init__.py",
)
"""The packages that resolve their exports lazily through PEP 562 `__getattr__`.

`flexi.domain` and `flexi.models` import theirs eagerly, so their annotations
come from the imports themselves and there is no second list to keep in step.
"""


def _names_bound_under_type_checking(tree: ast.Module) -> set[str]:
    """Every name the `if TYPE_CHECKING:` block imports, as it is bound."""
    bound: set[str] = set()
    for node in ast.walk(tree):
        guard = isinstance(node, ast.If) and isinstance(node.test, ast.Name)
        if not (guard and node.test.id == "TYPE_CHECKING"):  # type: ignore[attr-defined]
            continue
        for statement in ast.walk(node):
            if isinstance(statement, ast.Import | ast.ImportFrom):
                # `asname or name`: the facades rename on the way in
                # (`format as formatting`), and `__all__` holds the bound name.
                bound |= {alias.asname or alias.name for alias in statement.names}
    return bound


def _locally_defined(tree: ast.Module) -> set[str]:
    """Names the facade defines itself rather than re-exporting."""
    defined: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            defined.add(node.name)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.add(node.target.id)
        elif isinstance(node, ast.Assign):
            defined |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return defined


@pytest.mark.parametrize("relative", LAZY_FACADES)
def test_lazy_facade_types_everything_it_exports(relative: str) -> None:
    """The `if TYPE_CHECKING:` block is a fourth export list.

    A lazy facade carries the same names in `__all__`, the routing table,
    `__getattr__` and that block, and only the block is invisible at runtime: a
    name missing from it still imports, and only types as `object` downstream.
    """
    path = Path(__file__).resolve().parent.parent / "src" / relative
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    exported = set(_module_all(tree))
    missing = exported - _names_bound_under_type_checking(tree) - _locally_defined(tree)

    assert missing == set(), (
        f"{relative} exports {sorted(missing)} without importing them under "
        f"`if TYPE_CHECKING:`, so a type checker sees them as `object`"
    )


def _module_all(tree: ast.Module) -> list[str]:
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else []
        for target in targets:
            if isinstance(target, ast.Name) and target.id == "__all__":
                return [
                    element.value
                    for element in ast.walk(node)
                    if isinstance(element, ast.Constant)
                    and isinstance(element.value, str)
                ]
    msg = "the facade declares no __all__"
    raise AssertionError(msg)
