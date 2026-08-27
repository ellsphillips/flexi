"""The deliberate public surface of Flexi's persistence layer."""

from __future__ import annotations

import ast
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import get_type_hints

import pytest

from flexi import models
from flexi.models import database
from flexi.models.database import backup, db, engine, invariants, migrate, moment

LEAF_APIS: tuple[tuple[ModuleType, tuple[str, ...]], ...] = (
    (
        backup,
        ("PROTECTED_PREFIX", "ROUTINE_PREFIX", "snapshot", "verify"),
    ),
    (
        db,
        (
            "DEFAULT_CONTRACTED_MINUTES",
            "DEFAULT_WINDOW_END",
            "DEFAULT_WINDOW_START",
            "SETTINGS_SINGLETON_KEY",
            "AbsenceDay",
            "BalanceAdjustment",
            "BankHolidayCache",
            "Base",
            "ClockEvent",
            "LeaveEntitlement",
            "Settings",
            "WorkSession",
        ),
    ),
    (
        engine,
        (
            "create_db_engine",
            "database_scope",
            "enforce_foreign_keys",
            "get_session",
        ),
    ),
    (
        invariants,
        (
            "CLOCK_EVENT_UPDATE_ERROR",
            "CLOCK_EVENT_UPDATE_TRIGGER",
            "clock_event_update_trigger_sql",
            "create_clock_event_update_trigger",
            "drop_clock_event_update_trigger",
            "drop_clock_event_update_trigger_sql",
            "register_clock_event_immutability",
        ),
    ),
    (
        migrate,
        (
            "HEAD",
            "MAX_BACKUPS",
            "DatabaseRevision",
            "MigrationConfig",
            "RevisionState",
            "alembic_config",
            "backup_database",
            "current_revision",
            "prune_backups",
            "run_migrations",
        ),
    ),
    (moment, ("moment_of", "punched")),
)


def module_statements(statements: list[ast.stmt]) -> Iterator[ast.stmt]:
    """Statements evaluated at module scope, including conditional branches."""
    for statement in statements:
        yield statement
        if isinstance(statement, ast.If):
            yield from module_statements(statement.body)
            yield from module_statements(statement.orelse)


def target_names(target: ast.expr) -> Iterator[str]:
    """Names assigned by one module-level target."""
    if isinstance(target, ast.Name):
        yield target.id
    elif isinstance(target, ast.List | ast.Tuple):
        for item in target.elts:
            yield from target_names(item)


def locally_defined_public_names(module: ModuleType) -> set[str]:
    """Public values defined by a persistence leaf rather than imported."""
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
    """Names a real wildcard import receives from ``module``."""
    namespace: dict[str, object] = {}
    exec(f"from {module.__name__} import *", namespace)  # noqa: S102
    return set(namespace) - {"__builtins__"}


@pytest.mark.parametrize(
    ("module", "expected"),
    LEAF_APIS,
    ids=lambda value: value.__name__ if isinstance(value, ModuleType) else None,
)
def test_every_database_module_declares_an_immutable_api(
    module: ModuleType, expected: tuple[str, ...]
) -> None:
    assert module.__all__ == expected
    assert isinstance(module.__all__, tuple)
    assert len(module.__all__) == len(set(module.__all__))
    assert set(module.__all__) == locally_defined_public_names(module)
    assert wildcard_names(module) == set(expected)


@pytest.mark.parametrize("facade", [database, models])
def test_facades_export_only_their_declared_names(facade: ModuleType) -> None:
    assert isinstance(facade.__all__, tuple)
    assert len(facade.__all__) == len(set(facade.__all__))
    assert all(hasattr(facade, name) for name in facade.__all__)
    assert wildcard_names(facade) == set(facade.__all__)


def test_facades_preserve_every_deep_import() -> None:
    expected = {name for _module, names in LEAF_APIS for name in names}
    expected.update(module.__name__.rsplit(".", 1)[-1] for module, _names in LEAF_APIS)
    assert set(database.__all__) == expected
    assert set(models.__all__) == expected | {"database"}

    for module, names in LEAF_APIS:
        assert getattr(database, module.__name__.rsplit(".", 1)[-1]) is module
        assert getattr(models, module.__name__.rsplit(".", 1)[-1]) is module
        for name in names:
            implementation = getattr(module, name)
            assert getattr(database, name) is implementation
            assert getattr(models, name) is implementation

    assert models.database is database


def test_migration_config_annotation_is_resolvable_without_alembic() -> None:
    assert get_type_hints(migrate.alembic_config) == {
        "db_path": Path,
        "return": Iterator[migrate.MigrationConfig],
    }
    for name in migrate.__all__:
        exported = getattr(migrate, name)
        if callable(exported):
            get_type_hints(exported)


def test_importing_the_facades_and_annotations_does_not_import_alembic() -> None:
    """A fresh interpreter observes imports hidden by this test worker."""
    script = """
import sys
from typing import get_type_hints

import flexi.models
from flexi.models.database import migrate

get_type_hints(migrate.alembic_config)
loaded = sorted(
    name for name in sys.modules if name == "alembic" or name.startswith("alembic.")
)
if loaded:
    raise AssertionError(f"persistence facade imported Alembic: {loaded}")
"""
    subprocess.run(  # noqa: S603 - fixed interpreter and source-owned script
        [sys.executable, "-c", script], check=True
    )
