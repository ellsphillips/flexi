"""The supported API of Flexi's components, screens, and design system."""

from __future__ import annotations

import ast
import importlib
import subprocess
import sys
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import assert_type, get_type_hints
from unittest.mock import call, patch

import pytest

import flexi.components as component_api
import flexi.components.modules as module_api
import flexi.screens as screen_api
from flexi import theme
from flexi.components import Gauge as FacadeGauge
from flexi.components import Module as FacadeModule
from flexi.components import charts as facade_charts
from flexi.components import modules as facade_modules
from flexi.components import yearcalendar as facade_yearcalendar
from flexi.components.common import Gauge
from flexi.components.modules.base import Module
from flexi.screens import DashboardScreen as FacadeDashboardScreen
from flexi.screens.dashboard import DashboardScreen
from tests.public_api import contains_any, public_type_hints

COMPONENTS = Path(component_api.__file__).parent
SCREENS = Path(screen_api.__file__).parent

COMPONENT_MODULE_NAMES = tuple(
    path.stem for path in sorted(COMPONENTS.glob("*.py")) if path.stem != "__init__"
)
DASHBOARD_MODULE_NAMES = tuple(
    path.stem
    for path in sorted((COMPONENTS / "modules").glob("*.py"))
    if path.stem != "__init__"
)
SCREEN_MODULE_NAMES = tuple(
    path.stem for path in sorted(SCREENS.glob("*.py")) if path.stem != "__init__"
)

COMPONENT_LEAVES = tuple(
    importlib.import_module(f"flexi.components.{name}")
    for name in COMPONENT_MODULE_NAMES
)
DASHBOARD_MODULE_LEAVES = tuple(
    importlib.import_module(f"flexi.components.modules.{name}")
    for name in DASHBOARD_MODULE_NAMES
)
SCREEN_LEAVES = tuple(
    importlib.import_module(f"flexi.screens.{name}") for name in SCREEN_MODULE_NAMES
)
LEAVES = (*COMPONENT_LEAVES, *DASHBOARD_MODULE_LEAVES, *SCREEN_LEAVES, theme)


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
    """Public values defined by a leaf rather than imported into it."""
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


def owners_of(modules: tuple[ModuleType, ...]) -> dict[str, list[ModuleType]]:
    owners: defaultdict[str, list[ModuleType]] = defaultdict(list)
    for module in modules:
        for name in module.__all__:
            owners[name].append(module)
    return dict(owners)


@pytest.mark.parametrize("module", LEAVES, ids=lambda module: module.__name__)
def test_each_ui_leaf_publishes_every_local_name(module: ModuleType) -> None:
    assert isinstance(module.__all__, tuple)
    assert len(module.__all__) == len(set(module.__all__))
    assert set(module.__all__) == locally_defined_public_names(module)
    assert wildcard_names(module) == set(module.__all__)


@pytest.mark.parametrize("facade", [component_api, module_api, screen_api])
def test_facade_wildcards_export_only_the_declared_api(facade: ModuleType) -> None:
    assert isinstance(facade.__all__, tuple)
    assert len(facade.__all__) == len(set(facade.__all__))
    assert wildcard_names(facade) == set(facade.__all__)


def test_dashboard_module_facade_preserves_every_deep_import() -> None:
    owners = owners_of(DASHBOARD_MODULE_LEAVES)
    assert all(len(found) == 1 for found in owners.values())
    assert set(module_api.__all__) == set(DASHBOARD_MODULE_NAMES) | set(owners)

    for leaf in DASHBOARD_MODULE_LEAVES:
        module_name = leaf.__name__.rsplit(".", 1)[-1]
        assert getattr(module_api, module_name) is leaf
        for public_name in leaf.__all__:
            assert getattr(module_api, public_name) is getattr(leaf, public_name)


def test_component_facade_resolves_its_only_collision_semantically() -> None:
    leaves = (*COMPONENT_LEAVES, *DASHBOARD_MODULE_LEAVES)
    owners = owners_of(leaves)
    collisions = {
        name: [module.__name__.rsplit(".", 1)[-1] for module in found]
        for name, found in owners.items()
        if len(found) > 1
    }
    assert collisions == {"FULL": ["charts", "yearcalendar"]}

    module_names = {
        *COMPONENT_MODULE_NAMES,
        *DASHBOARD_MODULE_NAMES,
        "modules",
    }
    unique_names = {name for name, found in owners.items() if len(found) == 1}
    aliases = {"CHART_FULL_GLYPH", "FULL_DAY_GLYPH"}
    assert set(component_api.__all__) == module_names | unique_names | aliases

    for public_name in unique_names:
        [owner] = owners[public_name]
        assert getattr(component_api, public_name) is getattr(owner, public_name)
    assert component_api.CHART_FULL_GLYPH is facade_charts.FULL
    assert component_api.FULL_DAY_GLYPH is facade_yearcalendar.FULL
    assert component_api.modules is facade_modules is module_api

    for leaf in leaves:
        module_name = leaf.__name__.rsplit(".", 1)[-1]
        assert getattr(component_api, module_name) is leaf


def test_screen_facade_preserves_every_deep_import() -> None:
    owners = owners_of(SCREEN_LEAVES)
    assert all(len(found) == 1 for found in owners.values())
    assert set(screen_api.__all__) == set(SCREEN_MODULE_NAMES) | set(owners)

    for leaf in SCREEN_LEAVES:
        module_name = leaf.__name__.rsplit(".", 1)[-1]
        assert getattr(screen_api, module_name) is leaf
        for public_name in leaf.__all__:
            assert getattr(screen_api, public_name) is getattr(leaf, public_name)


def test_facades_are_statically_typed() -> None:
    assert assert_type(FacadeGauge, type[Gauge]) is Gauge
    assert assert_type(FacadeModule, type[Module]) is Module
    assert assert_type(FacadeDashboardScreen, type[DashboardScreen]) is DashboardScreen


def test_public_annotations_resolve_at_runtime() -> None:
    for module in LEAVES:
        checked = list(public_type_hints(module))
        assert checked
        for qualified, hints in checked:
            assert hints, f"{qualified} has no annotations"
            assert not any(map(contains_any, hints.values())), qualified

    common_hints = get_type_hints(component_api.styled_track)
    assert common_hints["track"].__module__ == "rich.style"
    assert get_type_hints(component_api.mark_width)["node"].__module__ == "textual.dom"
    assert get_type_hints(component_api.JumpOverlay.__init__)["jumper"] is (
        component_api.JumpOverlayProvider
    )

    for renderer in (
        component_api.Burndown,
        component_api.DivergingBars,
        component_api.Gauge,
        component_api.ProgressRail,
        component_api.PunchStrip,
        component_api.WeekRibbon,
        component_api.YearHeatmap,
    ):
        assert get_type_hints(renderer.render)["return"].__module__ == "rich.text"

    assert get_type_hints(theme.flexi_theme) == {"return": theme.Theme}


def test_every_dashboard_module_implements_the_rebuild_contract() -> None:
    implementations = Module.__subclasses__()
    assert implementations
    assert all(module.rebuild is not Module.rebuild for module in implementations)

    with pytest.raises(NotImplementedError):
        Module(id="contract", title="Contract").rebuild()


def test_lazy_results_are_cached() -> None:
    vars(component_api).pop("Gauge", None)
    vars(screen_api).pop("preview", None)

    with patch("importlib.import_module", wraps=importlib.import_module) as load:
        assert component_api.Gauge is Gauge
        assert component_api.Gauge is Gauge
        assert screen_api.preview is screen_api.leave.preview
        assert screen_api.preview is screen_api.leave.preview

    assert load.call_args_list == [
        call("flexi.components.common"),
        call("flexi.screens.leave"),
    ]


@pytest.mark.parametrize("facade", [component_api, module_api, screen_api])
def test_facade_discovery_and_unknown_attributes(facade: ModuleType) -> None:
    assert set(facade.__all__) <= set(dir(facade))
    name = "not_public"
    with pytest.raises(AttributeError, match="has no attribute 'not_public'"):
        getattr(facade, name)


def test_bare_ui_facades_and_theme_keep_the_heavy_graph_out() -> None:
    """A fresh interpreter makes the import budget observable."""
    script = """
import sys
from typing import get_type_hints

before = set(sys.modules)
import flexi.components
import flexi.screens
import flexi.theme

dir(flexi.components)
dir(flexi.screens)
get_type_hints(flexi.theme.flexi_theme)

introduced = set(sys.modules) - before
ui_modules = {
    name
    for name in introduced
    if name == "flexi.components"
    or name.startswith("flexi.components.")
    or name == "flexi.screens"
    or name.startswith("flexi.screens.")
}
expected = {"flexi.components", "flexi.screens"}
if ui_modules != expected:
    raise AssertionError(f"bare facades loaded UI leaves: {sorted(ui_modules)}")

heavy = {"alembic", "httpx", "rich", "sqlalchemy", "textual"}
loaded_heavy = {name.split(".")[0] for name in introduced}.intersection(heavy)
if loaded_heavy:
    message = f"bare UI API loaded heavy dependencies: {sorted(loaded_heavy)}"
    raise AssertionError(message)
"""
    subprocess.run(  # noqa: S603 - fixed interpreter and source-owned script
        [sys.executable, "-c", script], check=True
    )
