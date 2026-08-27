"""Cross-process ownership for one SQLite database.

SQLite coordinates statements, but a database lifecycle is wider than one
statement. A migration changes the schema beneath every existing engine, and a
reset takes a recovery snapshot before removing the live file. Those operations
must exclude application lifetimes, not merely hope no statement lands in the
gap between their own connections.

The lock lives beside the database and is never removed. Keeping one stable
inode is essential: deleting and recreating a lock file would let two processes
hold locks on different files with the same name.
"""

from __future__ import annotations

import errno
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from time import monotonic, sleep
from typing import BinaryIO

__all__ = (
    "DEFAULT_LEASE_TIMEOUT",
    "LEASE_POLL_INTERVAL",
    "DatabaseBusyError",
    "LeaseMode",
    "database_lease",
    "lease_path",
)

DEFAULT_LEASE_TIMEOUT = 1.0
"""Seconds an owner waits before reporting that another process is active."""

LEASE_POLL_INTERVAL = 0.05
"""Seconds between non-blocking attempts to acquire a contended lease."""


class LeaseMode(StrEnum):
    """Whether a database lifetime may coexist with other readers."""

    SHARED = "shared"
    EXCLUSIVE = "exclusive"


class DatabaseBusyError(RuntimeError):
    """An incompatible database owner remained active past the timeout."""

    def __init__(self, database: Path, mode: LeaseMode) -> None:
        self.database = database
        self.mode = mode
        super().__init__(
            f"Database is in use at {database}; close the other Flexi process "
            "and try again"
        )


def lease_path(database: Path) -> Path:
    """The stable coordination file belonging to ``database``."""
    return database.with_name(f"{database.name}.lock")


if sys.platform == "win32":  # pragma: no cover - exercised by the Windows job
    import msvcrt

    def _try_lock(handle: BinaryIO, mode: LeaseMode) -> bool:
        handle.seek(0)
        operation = msvcrt.LK_NBRLCK if mode is LeaseMode.SHARED else msvcrt.LK_NBLCK
        try:
            msvcrt.locking(handle.fileno(), operation, 1)
        except OSError as error:
            if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise
            return False
        return True

    def _unlock(handle: BinaryIO) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(handle: BinaryIO, mode: LeaseMode) -> bool:
        operation = fcntl.LOCK_SH if mode is LeaseMode.SHARED else fcntl.LOCK_EX
        try:
            fcntl.flock(handle.fileno(), operation | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def _unlock(handle: BinaryIO) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def database_lease(
    database: Path,
    mode: LeaseMode,
    *,
    timeout: float = DEFAULT_LEASE_TIMEOUT,
) -> Iterator[None]:
    """Hold a shared application lease or exclusive lifecycle lease.

    Acquisition is polled rather than left to an unbounded operating-system
    wait, so a reset against an open application gives a precise failure rather
    than appearing to hang. The binary file is initialised to one byte because
    Windows locks a byte range; POSIX locks the same stable file as a whole.
    """
    if timeout < 0:
        msg = "A database lease timeout cannot be negative"
        raise ValueError(msg)

    lock_file = lease_path(database)
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    deadline = monotonic() + timeout

    with lock_file.open("a+b") as handle:
        if lock_file.stat().st_size == 0:
            handle.write(b"\0")
            handle.flush()

        while not _try_lock(handle, mode):
            if monotonic() >= deadline:
                raise DatabaseBusyError(database, mode)
            sleep(min(LEASE_POLL_INTERVAL, max(0.0, deadline - monotonic())))

        try:
            yield
        finally:
            _unlock(handle)
