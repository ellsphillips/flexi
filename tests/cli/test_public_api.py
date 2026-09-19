"""The supported import surfaces of :mod:`flexi.cli` and its terminal UI."""

from __future__ import annotations

import importlib
import subprocess
import sys
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import assert_type, get_type_hints
from unittest.mock import call, patch

import click
import pytest

import flexi.cli as cli_api
import flexi.cli.ui as ui_api
from flexi.cli import Contents as FacadeContents
from flexi.cli import refresh_holidays
from flexi.cli.holidays import run as holidays_run
from flexi.cli.init import Contents
from flexi.cli.ui import Key as FacadeKey
from flexi.cli.ui.keys import Key
from flexi.services.registry import Services
from tests.public_api import (
    check_declared_api,
    check_public_annotations,
    locally_defined_public_names,
)

CLI = Path(cli_api.__file__).parent
UI = Path(ui_api.__file__).parent
CLI_MODULE_NAMES = tuple(
    path.stem for path in sorted(CLI.glob("*.py")) if path.stem != "__init__"
)
UI_MODULE_NAMES = tuple(
    path.stem for path in sorted(UI.glob("*.py")) if path.stem != "__init__"
)
LEAF_MODULES = tuple(f"flexi.cli.{name}" for name in CLI_MODULE_NAMES) + tuple(
    f"flexi.cli.ui.{name}" for name in UI_MODULE_NAMES
)

CLI_ROUTES = {
    "NO_CALENDAR": ("balance", "NO_CALENDAR"),
    "log": ("balance", "log"),
    "show": ("balance", "show"),
    "undo": ("balance", "undo"),
    "zero": ("balance", "zero"),
    "already_on": ("clock", "already_on"),
    "clock_in": ("clock", "clock_in"),
    "clock_out": ("clock", "clock_out"),
    "refresh_holidays": ("holidays", "run"),
    "CONFIRM_WORD": ("init", "CONFIRM_WORD"),
    "COUNTED": ("init", "COUNTED"),
    "Choice": ("init", "Choice"),
    "Contents": ("init", "Contents"),
    "READ_TIMEOUT": ("init", "READ_TIMEOUT"),
    "ask": ("init", "ask"),
    "confirm_reset": ("init", "confirm_reset"),
    "describe": ("init", "describe"),
    "options": ("init", "options"),
    "overview": ("init", "overview"),
    "reset": ("init", "reset"),
    "settled": ("init", "settled"),
    "PORTION_WORDS": ("leave", "PORTION_WORDS"),
    "Request": ("leave", "Request"),
    "VERDICT_NOTE": ("leave", "VERDICT_NOTE"),
    "cancel": ("leave", "cancel"),
    "manage_leave": ("leave", "run"),
    "parse_request": ("leave", "parse_request"),
    "render": ("leave", "render"),
    "PLAIN_TERMINALS": ("output", "PLAIN_TERMINALS"),
    "enable_ansi": ("output", "enable_ansi"),
    "monochrome": ("output", "monochrome"),
    "prepare": ("output", "prepare"),
    "tolerant": ("output", "tolerant"),
}


@pytest.mark.parametrize("qualified_name", LEAF_MODULES)
def test_every_leaf_declares_its_complete_local_api(qualified_name: str) -> None:
    check_declared_api(importlib.import_module(qualified_name))


@pytest.mark.parametrize(
    ("qualified_name", "module"),
    [("flexi.cli", cli_api), ("flexi.cli.ui", ui_api)],
)
def test_facade_wildcards_export_only_the_declared_api(
    qualified_name: str,
    module: ModuleType,
) -> None:
    namespace: dict[str, object] = {}

    exec(f"from {qualified_name} import *", namespace)  # noqa: S102

    exported = {name for name in namespace if not name.startswith("_")}
    assert exported == set(module.__all__)


def test_cli_facade_routes_every_leaf_export_once() -> None:
    modules = {
        name: importlib.import_module(f"flexi.cli.{name}") for name in CLI_MODULE_NAMES
    }
    expected_sources = {
        (module_name, public_name)
        for module_name, module in modules.items()
        for public_name in module.__all__
    }

    assert set(CLI_ROUTES.values()) == expected_sources
    assert all(count == 1 for count in Counter(CLI_ROUTES.values()).values())
    assert locally_defined_public_names(cli_api) == {
        "TypedDate",
        "Utf8Text",
        "report",
    }
    expected_facade = {
        "TypedDate",
        "Utf8Text",
        "report",
        "ui",
        *CLI_MODULE_NAMES,
        *CLI_ROUTES,
    }
    assert isinstance(cli_api.__all__, tuple)
    assert set(cli_api.__all__) == expected_facade
    assert len(cli_api.__all__) == len(set(cli_api.__all__))

    for facade_name, (module_name, public_name) in CLI_ROUTES.items():
        assert getattr(cli_api, facade_name) is getattr(
            modules[module_name], public_name
        )
    for module_name, module in modules.items():
        assert getattr(cli_api, module_name) is module
    assert cli_api.ui is ui_api


def test_ui_facade_routes_every_export_once() -> None:
    owners: defaultdict[str, list[str]] = defaultdict(list)
    modules = {
        name: importlib.import_module(f"flexi.cli.ui.{name}")
        for name in UI_MODULE_NAMES
    }
    for module_name, module in modules.items():
        for public_name in module.__all__:
            owners[public_name].append(module_name)

    collisions = {name: found for name, found in owners.items() if len(found) > 1}
    assert collisions == {}
    assert isinstance(ui_api.__all__, tuple)
    assert set(ui_api.__all__) == set(UI_MODULE_NAMES) | set(owners)
    assert len(ui_api.__all__) == len(set(ui_api.__all__))

    for module_name, module in modules.items():
        assert getattr(ui_api, module_name) is module
        for public_name in module.__all__:
            assert getattr(ui_api, public_name) is getattr(module, public_name)


def test_facades_are_static_and_runtime_typed() -> None:
    refresh: Callable[[Services], int] = refresh_holidays

    assert assert_type(FacadeContents, type[Contents]) is Contents
    assert assert_type(FacadeKey, type[Key]) is Key
    assert refresh is holidays_run


@pytest.mark.parametrize("typed", ["+999999999d", "-999999999w"])
def test_extreme_date_offsets_are_usage_errors(typed: str) -> None:
    with pytest.raises(click.BadParameter, match="outside"):
        cli_api.TypedDate().convert(typed, None, None)


def test_text_sqlite_cannot_store_is_refused() -> None:
    """Python decodes argv with `surrogateescape`.

    A cp1252 note pasted into a UTF-8 process arrives as a lone surrogate, which
    SQLite refuses.
    """
    with pytest.raises(click.BadParameter, match="not valid UTF-8"):
        cli_api.Utf8Text().convert("caf\udce9", None, None)


def test_storable_free_text_passes_through() -> None:
    assert cli_api.Utf8Text().convert("café ☕", None, None) == "café ☕"


def test_public_annotations_resolve_at_runtime() -> None:
    modules = [importlib.import_module(name) for name in LEAF_MODULES]
    modules.append(cli_api)

    for module in modules:
        check_public_annotations(module)

    assert get_type_hints(cli_api.TypedDate.convert)["return"] is date


def test_lazy_results_are_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(vars(cli_api), "refresh_holidays", raising=False)
    monkeypatch.delitem(vars(ui_api), "Key", raising=False)

    with patch("importlib.import_module", wraps=importlib.import_module) as load:
        assert cli_api.refresh_holidays is holidays_run
        assert cli_api.refresh_holidays is holidays_run
        assert ui_api.Key is Key
        assert ui_api.Key is Key

    assert load.call_args_list == [
        call("flexi.cli.holidays"),
        call("flexi.cli.ui.keys"),
    ]


def test_dir_lists_unresolved_public_names() -> None:
    assert set(cli_api.__all__) <= set(dir(cli_api))
    assert set(ui_api.__all__) <= set(dir(ui_api))


@pytest.mark.parametrize("module", [cli_api, ui_api])
def test_unknown_facade_attributes_still_raise(module: object) -> None:
    name = "not_public"
    with pytest.raises(AttributeError, match="has no attribute 'not_public'"):
        getattr(module, name)


def test_light_ui_import_loads_nothing_heavy() -> None:
    """A fresh interpreter makes the dependency budget observable."""
    script = """
import sys

before = set(sys.modules)
import flexi.cli

dir(flexi.cli)
import flexi.cli.ui

dir(flexi.cli.ui)
import flexi.cli.ui.keys

introduced = set(sys.modules) - before
cli_modules = {name for name in introduced if name.startswith("flexi.cli")}
expected = {"flexi.cli", "flexi.cli.ui", "flexi.cli.ui.keys"}
if cli_modules != expected:
    raise AssertionError(f"light UI import loaded the CLI graph: {sorted(cli_modules)}")

heavy = {"alembic", "httpx", "rich", "sqlalchemy", "textual"}
loaded_heavy = {name.split(".")[0] for name in introduced}.intersection(heavy)
if loaded_heavy:
    raise AssertionError(f"light UI import loaded heavy deps: {sorted(loaded_heavy)}")
"""
    subprocess.run(  # noqa: S603 - fixed interpreter and in-repository script
        [sys.executable, "-c", script], check=True
    )


def test_date_grammar_is_imported_only_on_conversion() -> None:
    """`flexi --version` builds two `ParamType`s and converts nothing with them.

    The entry point imports this package at module scope for `TypedDate` and
    `Utf8Text`, so the date grammar loads on the first conversion instead.
    """
    script = """
import sys

import flexi.cli

if "flexi.domain.dates" in sys.modules:
    raise AssertionError("importing flexi.cli loaded the date grammar")

flexi.cli.TypedDate().convert("2026-06-11", None, None)
if "flexi.domain.dates" not in sys.modules:
    raise AssertionError("converting a date did not reach the grammar")
"""
    subprocess.run(  # noqa: S603 - fixed interpreter and in-repository script
        [sys.executable, "-c", script], check=True
    )
