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
                # `asname or name`, because the facades deliberately rename on
                # the way in -- `format as formatting`, `FULL as
                # CHART_FULL_GLYPH` -- and it is the bound name that has to
                # match `__all__`.
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
def test_a_lazy_facade_types_everything_it_exports(relative: str) -> None:
    """The `if TYPE_CHECKING:` block is a fourth export list, and nothing gated it.

    A lazy facade carries the same names in four places: `__all__`, the runtime
    routing table, `__getattr__`, and this block. Only the block is invisible at
    runtime -- a name missing from it still imports and still works, and the
    only symptom is that `from flexi.services import CORRECTION_OVERLAP` types
    as `object` for anybody downstream. Flexi ships `py.typed`, so that is a
    hole in a contract it makes explicitly.

    Six names had already drifted out of `flexi.services` and one out of
    `flexi.components`, which is what a hand-maintained list with no gate does.

    `if TYPE_CHECKING:` is in coverage's `exclude_also`, so this costs the
    100% gate nothing.
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
