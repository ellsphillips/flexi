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

from flexi.locations import ensure

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
    """The stable coordination file belonging to the resolved database.

    A symlink is another route to the same SQLite file, so it must share the
    real file's lease. Otherwise a migration through one name can run while an
    application holds a lease through the other.
    """
    database = database.resolve()
    return database.with_name(f"{database.name}.lock")


if sys.platform == "win32":  # pragma: no cover - Windows only
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
    # Without argument declarations ctypes converts Python integers to C int,
    # which is narrower than HANDLE on 64-bit Windows.
    _kernel32.LockFileEx.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_Overlapped),
    )
    _kernel32.LockFileEx.restype = wintypes.BOOL
    _kernel32.UnlockFileEx.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_Overlapped),
    )
    _kernel32.UnlockFileEx.restype = wintypes.BOOL

    def _try_lock(handle: BinaryIO, mode: LeaseMode) -> bool:
        """Take a real shared or exclusive lock on the lease file's first byte.

        `msvcrt.locking` has no shared mode: Microsoft documents `_LK_NBRLCK`
        as "same as `_LK_NBLCK`". `LockFileEx` distinguishes the two, and
        without `LOCKFILE_EXCLUSIVE_LOCK` it takes the shared lock that the
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
            # An incompatible holder. Any other code is a real fault and must
            # not reach the caller as mere contention.
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

    Acquisition is polled, not left to an unbounded operating-system wait, so a
    reset against an open application reports a busy database instead of
    appearing to hang. The file is initialised to one byte because Windows locks
    a byte range; POSIX locks the same stable file as a whole.
    """
    if timeout < 0:
        msg = "A database lease timeout cannot be negative"
        raise ValueError(msg)
    if not isfinite(timeout):
        msg = "A database lease timeout must be finite"
        raise ValueError(msg)

    lock_file = lease_path(database)
    # Through `ensure`, like every other writer: the lease can be the first
    # thing to create the data directory, and a default umask would leave it
    # readable by every other account on the machine.
    ensure(lock_file.parent)
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
