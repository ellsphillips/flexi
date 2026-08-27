"""The transaction boundary shared by every service write.

SQLAlchemy leaves a session unusable after a failed flush or commit until it is
rolled back.  Service methods should not make every caller know that persistence
detail, and duplicating the recovery around each commit is exactly how most
writes ended up omitting it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from functools import partial

from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import Session

__all__ = (
    "WriteTransaction",
    "atomic",
    "bind_write_transaction",
    "write_transaction",
)

type WriteTransaction = Callable[[], AbstractContextManager[None]]
"""A persistence-agnostic boundary that reserves and commits one write."""


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


def bind_write_transaction(session: Session) -> WriteTransaction:
    """Bind a session once without exposing it to orchestration code."""
    return partial(write_transaction, session)
