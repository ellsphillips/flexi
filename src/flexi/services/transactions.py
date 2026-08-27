"""The transaction boundary shared by every service write.

SQLAlchemy leaves a session unusable after a failed flush or commit until it is
rolled back.  Service methods should not make every caller know that persistence
detail, and duplicating the recovery around each commit is exactly how most
writes ended up omitting it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session

__all__ = ("atomic",)


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
