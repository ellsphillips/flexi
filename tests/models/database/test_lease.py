"""Cross-process ownership of a database lifecycle."""

from __future__ import annotations

from pathlib import Path

import pytest

from flexi.models.database.engine import database_scope
from flexi.models.database.lease import (
    DatabaseBusyError,
    LeaseMode,
    database_lease,
    lease_path,
)


def test_the_lease_has_one_stable_path_beside_the_database(tmp_path: Path) -> None:
    database = tmp_path / "records.db"

    assert lease_path(database) == tmp_path / "records.db.lock"


def test_application_lifetimes_may_share_a_database(tmp_path: Path) -> None:
    database = tmp_path / "records.db"

    with (
        database_lease(database, LeaseMode.SHARED),
        database_lease(database, LeaseMode.SHARED),
    ):
        assert lease_path(database).read_bytes() == b"\0"


def test_an_exclusive_owner_refuses_an_active_application(tmp_path: Path) -> None:
    database = tmp_path / "records.db"

    with (
        database_lease(database, LeaseMode.SHARED),
        pytest.raises(DatabaseBusyError) as caught,
        database_lease(database, LeaseMode.EXCLUSIVE, timeout=0.01),
    ):
        pytest.fail("exclusive ownership cannot overlap an application")

    assert caught.value.database == database
    assert caught.value.mode is LeaseMode.EXCLUSIVE
    assert "in use" in str(caught.value)


def test_an_exclusive_lease_is_reusable_after_release(tmp_path: Path) -> None:
    database = tmp_path / "records.db"

    with database_lease(database, LeaseMode.EXCLUSIVE):
        pass
    with database_lease(database, LeaseMode.EXCLUSIVE):
        pass


def test_a_negative_wait_is_rejected(tmp_path: Path) -> None:
    with (
        pytest.raises(ValueError, match="cannot be negative"),
        database_lease(tmp_path / "records.db", LeaseMode.SHARED, timeout=-0.01),
    ):
        pytest.fail("an invalid lease cannot be entered")


@pytest.mark.parametrize("timeout", [float("nan"), float("inf")])
def test_a_non_finite_wait_is_rejected(tmp_path: Path, timeout: float) -> None:
    with (
        pytest.raises(ValueError, match="must be finite"),
        database_lease(tmp_path / "records.db", LeaseMode.SHARED, timeout=timeout),
    ):
        pytest.fail("an unbounded lease wait cannot be entered")


def test_database_scope_holds_a_shared_lease_until_cleanup(tmp_path: Path) -> None:
    database = tmp_path / "records.db"

    with (
        database_scope(database),
        pytest.raises(DatabaseBusyError),
        database_lease(database, LeaseMode.EXCLUSIVE, timeout=0),
    ):
        pytest.fail("a live database scope must exclude lifecycle changes")

    with database_lease(database, LeaseMode.EXCLUSIVE, timeout=0):
        pass
