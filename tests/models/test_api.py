"""The deliberate public surface of Flexi's persistence layer."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import get_type_hints

import pytest

from flexi import models
from flexi.models import database
from flexi.models.database import backup, db, engine, migrate, moment

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
        ("create_db_engine", "enforce_foreign_keys", "get_session"),
    ),
    (
        migrate,
        (
            "HEAD",
            "MAX_BACKUPS",
            "MigrationConfig",
            "alembic_config",
            "backup_database",
            "current_revision",
            "prune_backups",
            "run_migrations",
        ),
    ),
    (moment, ("moment_of", "punched")),
)


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
