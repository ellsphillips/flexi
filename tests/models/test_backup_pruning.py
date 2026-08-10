"""Which backups the pruner is allowed to take.

Ten routine snapshots accumulate over a fortnight of ordinary upgrades, because
one is taken before every migration. The snapshot written before a reset is not
routine: it is the only copy of records somebody chose to erase, and `flexi init`
tells them so before asking them to type the word. A pruner that sorts by age
alone deletes exactly that file, and does it soonest for the people who reset
longest ago.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from flexi.models.database import migrate
from flexi.models.database.backup import PROTECTED_PREFIX


@pytest.fixture
def backups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "backups"
    directory.mkdir()
    monkeypatch.setattr(migrate, "backups_directory", lambda: directory)
    return directory


def routine(directory: Path, count: int) -> None:
    """More migration backups than the pruner is willing to keep."""
    for n in range(count):
        path = directory / f"db_2026{n:04d}T000000Z.bak"
        path.write_bytes(b"routine")
        os.utime(path, (1_000_000 + n, 1_000_000 + n))


def test_it_keeps_only_the_most_recent_routine_backups(backups: Path) -> None:
    routine(backups, migrate.MAX_BACKUPS + 5)
    migrate._cleanup_old_backups()
    assert len(list(backups.glob("*.bak"))) == migrate.MAX_BACKUPS


def test_the_snapshot_taken_before_a_reset_is_never_pruned(backups: Path) -> None:
    """Being the oldest file there is what makes it the one at risk."""
    protected = backups / f"{PROTECTED_PREFIX}db_20260101T000000Z.bak"
    protected.write_bytes(b"the only copy of the erased records")
    os.utime(protected, (0, 0))

    routine(backups, migrate.MAX_BACKUPS + 5)
    migrate._cleanup_old_backups()

    assert protected.is_file(), "the one file that cannot be recreated was pruned"


def test_protected_snapshots_do_not_use_up_the_allowance(backups: Path) -> None:
    """Somebody who has reset twice still keeps ten routine backups."""
    for n in range(2):
        kept = backups / f"{PROTECTED_PREFIX}db_2026010{n}T000000Z.bak"
        kept.write_bytes(b"protected")
        os.utime(kept, (n, n))

    routine(backups, migrate.MAX_BACKUPS + 5)
    migrate._cleanup_old_backups()

    survivors = sorted(p.name for p in backups.glob("*.bak"))
    assert sum(name.startswith(PROTECTED_PREFIX) for name in survivors) == 2
    assert sum(not name.startswith(PROTECTED_PREFIX) for name in survivors) == (
        migrate.MAX_BACKUPS
    )
