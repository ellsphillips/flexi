"""The supported, lazy import surface of :mod:`flexi.services`."""

from __future__ import annotations

import ast
import importlib
import subprocess
import sys
from collections import defaultdict
from collections.abc import Callable, Iterator
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from typing import assert_type, get_type_hints
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

import flexi.services as service_api
from flexi.services import Services as FacadeServices
from flexi.services import build_services as facade_build_services
from flexi.services.registry import Services as RegistryServices
from flexi.services.registry import (
    available_toil_days,
    build_services,
    invalidate_services,
    settlement_date,
    zero_balance,
)

SERVICES = Path(service_api.__file__).parent
MODULE_NAMES = tuple(
    path.stem for path in sorted(SERVICES.glob("*.py")) if path.stem != "__init__"
)


def target_names(target: ast.expr) -> Iterator[str]:
    """Names assigned by one module-level target, including tuple unpacking."""
    if isinstance(target, ast.Name):
        yield target.id
    elif isinstance(target, ast.List | ast.Tuple):
        for item in target.elts:
            yield from target_names(item)


def locally_defined_public_names(path: Path) -> set[str]:
    """Public names the module defines itself rather than imports."""
    found: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for statement in tree.body:
        if isinstance(
            statement,
            ast.AsyncFunctionDef | ast.ClassDef | ast.FunctionDef,
        ):
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


@pytest.mark.parametrize("module_name", MODULE_NAMES)
def test_each_service_module_exports_every_local_public_name(module_name: str) -> None:
    module = importlib.import_module(f"flexi.services.{module_name}")
    declared = module.__all__

    assert isinstance(declared, tuple)
    assert len(declared) == len(set(declared))
    assert set(declared) == locally_defined_public_names(SERVICES / f"{module_name}.py")


def test_the_facade_has_one_unambiguous_route_to_every_export() -> None:
    owners: defaultdict[str, list[str]] = defaultdict(list)
    modules = {
        name: importlib.import_module(f"flexi.services.{name}") for name in MODULE_NAMES
    }
    for module_name, module in modules.items():
        for public_name in module.__all__:
            owners[public_name].append(module_name)

    collisions = {name: found for name, found in owners.items() if len(found) > 1}
    assert collisions == {}
    assert set(service_api.__all__) == set(MODULE_NAMES) | set(owners)
    assert len(service_api.__all__) == len(set(service_api.__all__))

    for module_name, module in modules.items():
        assert getattr(service_api, module_name) is module
        for public_name in module.__all__:
            assert getattr(service_api, public_name) is getattr(module, public_name)


def test_composition_api_is_available_from_the_typed_facade() -> None:
    factory: Callable[[Session], RegistryServices] = facade_build_services

    assert assert_type(FacadeServices, type[RegistryServices]) is RegistryServices
    assert factory is build_services


def test_services_is_a_frozen_session_free_data_bundle(session: Session) -> None:
    services = build_services(session)

    assert {field.name for field in fields(services)} == {
        "absence",
        "adjustments",
        "bank_holidays",
        "clock",
        "ledger",
        "settings",
        "wallet",
        "write",
    }
    assert not hasattr(services, "session")
    assert not hasattr(services.wallet, "_session")
    assert not {
        "available_toil_days",
        "build",
        "invalidate",
        "settles_to",
        "zero_balance",
    }.intersection(vars(RegistryServices))

    name = "wallet"
    with pytest.raises(FrozenInstanceError):
        setattr(services, name, services.wallet)


def test_composition_function_annotations_resolve() -> None:
    for operation in (
        available_toil_days,
        build_services,
        invalidate_services,
        settlement_date,
        zero_balance,
    ):
        assert get_type_hints(operation)


def test_a_lazily_resolved_symbol_is_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delitem(vars(service_api), "build_services", raising=False)
    with patch("importlib.import_module", wraps=importlib.import_module) as load:
        assert service_api.build_services is build_services
        assert service_api.build_services is build_services

    load.assert_called_once_with("flexi.services.registry")


def test_dir_lists_unresolved_public_names() -> None:
    assert set(service_api.__all__) <= set(dir(service_api))


def test_an_unknown_service_attribute_still_raises() -> None:
    name = "not_a_service"
    with pytest.raises(
        AttributeError,
        match=r"module 'flexi.services' has no attribute 'not_a_service'",
    ):
        getattr(service_api, name)


def test_setup_import_stays_independent_of_the_service_graph() -> None:
    """Use a fresh interpreter so this worker's earlier imports cannot hide one."""
    script = """
import sys

before = set(sys.modules)
import flexi.services

dir(flexi.services)
import flexi.services.setup

introduced = set(sys.modules) - before
service_modules = {
    name
    for name in introduced
    if name == "flexi.services" or name.startswith("flexi.services.")
}
expected = {"flexi.services", "flexi.services.setup"}
if service_modules != expected:
    raise AssertionError(f"setup loaded the service graph: {sorted(service_modules)}")

heavy = {"alembic", "httpx", "sqlalchemy", "textual"}
loaded_heavy = {name.split(".")[0] for name in introduced}.intersection(heavy)
if loaded_heavy:
    raise AssertionError(f"setup loaded heavy dependencies: {sorted(loaded_heavy)}")
"""
    subprocess.run(  # noqa: S603 - fixed interpreter and in-repository script
        [sys.executable, "-c", script], check=True
    )
