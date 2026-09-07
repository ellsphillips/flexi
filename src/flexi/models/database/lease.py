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

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from enum import StrEnum
from math import isfinite
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
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _LOCKFILE_FAIL_IMMEDIATELY = 0x1
    _LOCKFILE_EXCLUSIVE_LOCK = 0x2
    _ERROR_LOCK_VIOLATION = 33
    _ERROR_IO_PENDING = 997

    class _Overlapped(ctypes.Structure):
        """The byte range `LockFileEx` works on: offset zero, as POSIX does."""

        _fields_ = (
            ("Internal", wintypes.LPVOID),
            ("InternalHigh", wintypes.LPVOID),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        )

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def _try_lock(handle: BinaryIO, mode: LeaseMode) -> bool:
        """Take a real shared or exclusive lock on the lease file's first byte.

        `msvcrt.locking` was used here, with `LK_NBRLCK` for a shared lease --
        but Microsoft documents `_LK_NBRLCK` as "same as `_LK_NBLCK`", and
        `_locking` exposes no shared mode at all. Every lease on Windows was
        therefore exclusive: two application lifetimes could not share a
        database, so `flexi clock in` could not run while the TUI was open, and
        `tests/models/database/test_lease.py` failed on all six Windows rows of
        the matrix.

        `LockFileEx` is the API that distinguishes the two. Without
        `LOCKFILE_EXCLUSIVE_LOCK` it takes a shared lock, which is what the
        POSIX branch below gets from `LOCK_SH`.
        """
        flags = _LOCKFILE_FAIL_IMMEDIATELY
        if mode is LeaseMode.EXCLUSIVE:
            flags |= _LOCKFILE_EXCLUSIVE_LOCK
        overlapped = _Overlapped()
        taken = _kernel32.LockFileEx(
            msvcrt.get_osfhandle(handle.fileno()),
            flags,
            0,
            1,
            0,
            ctypes.byref(overlapped),
        )
        if not taken:
            code = ctypes.get_last_error()
            # Held by somebody incompatible. Anything else is a real fault and
            # must not be reported to the caller as mere contention.
            if code in {_ERROR_LOCK_VIOLATION, _ERROR_IO_PENDING}:
                return False
            raise ctypes.WinError(code)
        return True

    def _unlock(handle: BinaryIO) -> None:
        overlapped = _Overlapped()
        released = _kernel32.UnlockFileEx(
            msvcrt.get_osfhandle(handle.fileno()), 0, 1, 0, ctypes.byref(overlapped)
        )
        if not released:
            raise ctypes.WinError(ctypes.get_last_error())

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
    if not isfinite(timeout):
        msg = "A database lease timeout must be finite"
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
