"""The transaction boundary shared by every service write.

SQLAlchemy leaves a session unusable after a failed flush or commit until it is
rolled back.  Service methods should not make every caller know that persistence
detail, and duplicating the recovery around each commit is exactly how most
writes ended up omitting it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import Session

__all__ = ("atomic", "write_transaction")


@contextmanager
def atomic(session: Session) -> Iterator[None]:
    """Commit the enclosed unit once, rolling it back on any failure.

    Mutations and explicit flushes belong inside the context so an exception
    from either is recovered just like an exception from ``commit``.  Catching
    :class:`BaseException` is deliberate: interruption must not strand pending
    writes in a long-lived application session.
    """
    try:
        yield
        session.commit()
    except BaseException:
        session.rollback()
        raise


@contextmanager
def write_transaction(session: Session) -> Iterator[None]:
    """Reserve SQLite's writer before reading a decision that will be stored.

    A normal deferred transaction can read a valid state, let another process
    change it, and then persist a decision made from the stale read. ``BEGIN
    IMMEDIATE`` acquires SQLite's write reservation before the enclosed reads,
    so no other writer can invalidate them before :func:`atomic` commits.

    The shared session may already hold a read transaction from a preview. It
    is safely ended first, but pending mutations are never discarded silently.
    """
    if session.new or session.dirty or session.deleted:
        message = "a write transaction requires a session with no pending changes"
        raise InvalidRequestError(message)

    session.rollback()
    try:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
    except BaseException:
        session.rollback()
        raise

    with atomic(session):
        yield
