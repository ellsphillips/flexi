"""Which backups the pruner is allowed to take.

One snapshot is taken before every migration, so routine backups accumulate.
The snapshot written before a reset is not routine: it is the only copy of the
erased records, and sorting by age alone deletes it first.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from flexi.models.database import migrate
from flexi.models.database.backup import PROTECTED_PREFIX


@pytest.fixture
def backups(tmp_path: Path) -> Path:
    """The directory the pruner is pointed at, passed as an argument."""
    directory = tmp_path / "backups"
    directory.mkdir()
    return directory


def routine(directory: Path, count: int) -> None:
    """More migration backups than the pruner is willing to keep."""
    for n in range(count):
        path = directory / f"db_2026{n:04d}T000000Z.bak"
        path.write_bytes(b"routine")
        os.utime(path, (1_000_000 + n, 1_000_000 + n))


def test_max_backups_is_ten() -> None:
    """The number README's "Your data" promises; the rest read the constant."""
    assert migrate.MAX_BACKUPS == 10


def test_only_the_newest_routine_backups_survive(backups: Path) -> None:
    routine(backups, migrate.MAX_BACKUPS + 5)
    migrate.prune_backups(backups)
    assert len(list(backups.glob("*.bak"))) == migrate.MAX_BACKUPS


def test_reset_snapshot_is_never_pruned(backups: Path) -> None:
    """Being the oldest file there is what makes it the one at risk."""
    protected = backups / f"{PROTECTED_PREFIX}db_20260101T000000Z.bak"
    protected.write_bytes(b"the only copy of the erased records")
    os.utime(protected, (0, 0))

    routine(backups, migrate.MAX_BACKUPS + 5)
    migrate.prune_backups(backups)

    assert protected.is_file(), "the one file that cannot be recreated was pruned"


def test_backup_just_taken_is_kept(
    backups: Path,
) -> None:
    """A restored directory can date the copy `keep` names behind every other."""
    routine(backups, migrate.MAX_BACKUPS)
    fresh = backups / "db_20200101T000000Z.bak"
    fresh.write_bytes(b"the copy this upgrade depends on")
    os.utime(fresh, (0, 0))

    migrate.prune_backups(backups, keep=fresh)

    assert fresh.is_file(), "the copy the upgrade depends on was pruned"
    assert len(list(backups.glob("*.bak"))) == migrate.MAX_BACKUPS


def test_protected_snapshots_do_not_use_the_allowance(backups: Path) -> None:
    """A user who has reset twice still keeps `MAX_BACKUPS` routine backups."""
    for n in range(2):
        kept = backups / f"{PROTECTED_PREFIX}db_2026010{n}T000000Z.bak"
        kept.write_bytes(b"protected")
        os.utime(kept, (n, n))

    routine(backups, migrate.MAX_BACKUPS + 5)
    migrate.prune_backups(backups)

    survivors = sorted(p.name for p in backups.glob("*.bak"))
    assert sum(name.startswith(PROTECTED_PREFIX) for name in survivors) == 2
    assert sum(not name.startswith(PROTECTED_PREFIX) for name in survivors) == (
        migrate.MAX_BACKUPS
    )


def test_failed_prune_warns_without_raising(
    backups: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Housekeeping runs after the backup it is tidying up for has been taken.

    A full disk or a read-only directory must not turn a successful migration
    into a failed one, and a silent pruner reaches a hundred backups.
    """

    def refuse(_self: Path, **_kwargs: object) -> None:
        msg = "No space left on device"
        raise OSError(msg)

    routine(backups, migrate.MAX_BACKUPS + 5)
    monkeypatch.setattr(Path, "unlink", refuse)

    with caplog.at_level(logging.WARNING):
        migrate.prune_backups(backups)

    assert len(list(backups.glob("*.bak"))) == migrate.MAX_BACKUPS + 5
    assert "could not prune old backups" in caplog.text
    assert caplog.records[0].exc_info is not None, (
        "the traceback is the only clue to why pruning stopped"
    )
