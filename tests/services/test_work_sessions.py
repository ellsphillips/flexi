"""Conditional work-session writes preserve the immutable event trail."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Engine, event, select
from sqlalchemy.orm import Session

from flexi.constants import ClockAction, EventSource
from flexi.models.database.db import ClockEvent, WorkSession
from flexi.models.database.moment import punched
from flexi.services.transactions import atomic
from flexi.services.work_sessions import stage_clock_in, stage_clock_out

MONDAY = date(2026, 8, 10)
NINE = datetime(2026, 8, 10, 9, tzinfo=UTC)


@contextmanager
def sent_to(engine: Engine) -> Iterator[list[str]]:
    """Record every statement the engine sends while the block runs."""
    seen: list[str] = []

    def record(*args: object) -> None:
        seen.append(str(args[2]))

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_two_writers_cannot_both_open_a_session(engine: Engine) -> None:
    with Session(engine) as first, Session(engine) as second:
        with atomic(first):
            first_session = stage_clock_in(
                first,
                NINE,
                MONDAY,
                source=EventSource.USER,
            )
        with atomic(second):
            second_session = stage_clock_in(
                second,
                NINE + timedelta(minutes=1),
                MONDAY,
                source=EventSource.SYSTEM,
            )

    with Session(engine) as check:
        work_sessions = check.scalars(select(WorkSession)).all()
        clock_ins = check.scalars(
            select(ClockEvent).where(ClockEvent.action == ClockAction.IN)
        ).all()

    assert first_session is not None
    assert second_session is None
    assert len(work_sessions) == 1
    assert len(clock_ins) == 1
    assert work_sessions[0].clock_in_id == clock_ins[0].id


def test_two_writers_cannot_both_close_one_session(engine: Engine) -> None:
    with Session(engine) as seed, atomic(seed):
        clock_in = punched(ClockAction.IN, NINE, source=EventSource.USER)
        seed.add(clock_in)
        seed.flush()
        seed.add(WorkSession(clock_in_id=clock_in.id, work_date=MONDAY))

    with Session(engine) as first, Session(engine) as second:
        first_id = first.scalar(select(WorkSession.id))
        second_id = second.scalar(select(WorkSession.id))
        assert first_id is not None
        assert second_id == first_id

        with atomic(first):
            first_closed = stage_clock_out(
                first,
                first_id,
                NINE + timedelta(hours=8),
                source=EventSource.USER,
            )
        with atomic(second):
            second_closed = stage_clock_out(
                second,
                second_id,
                NINE + timedelta(hours=9),
                source=EventSource.SYSTEM,
            )

    with Session(engine) as check:
        work_session = check.scalars(select(WorkSession)).one()
        clock_outs = check.scalars(
            select(ClockEvent).where(ClockEvent.action == ClockAction.OUT)
        ).all()

    assert (first_closed, second_closed) == (True, False)
    assert len(clock_outs) == 1
    assert work_session.clock_out_id == clock_outs[0].id


def test_voided_session_cannot_claim_a_clock_out(engine: Engine) -> None:
    """The conditional primitive shares the active-session invariant."""
    with Session(engine) as session, atomic(session):
        clock_in = punched(ClockAction.IN, NINE, source=EventSource.USER)
        session.add(clock_in)
        session.flush()
        session.add(
            WorkSession(
                clock_in_id=clock_in.id,
                work_date=MONDAY,
                voided=True,
            )
        )

    with Session(engine) as session, atomic(session):
        work_session_id = session.scalar(select(WorkSession.id))
        assert work_session_id is not None
        closed = stage_clock_out(
            session,
            work_session_id,
            NINE + timedelta(hours=8),
            source=EventSource.USER,
        )

    with Session(engine) as check:
        clock_outs = check.scalars(
            select(ClockEvent).where(ClockEvent.action == ClockAction.OUT)
        ).all()

    assert closed is False
    assert clock_outs == []


def test_clocking_asks_for_no_syntax_sqlite_3_34_lacks(engine: Engine) -> None:
    """RETURNING arrived in SQLite 3.35; RHEL 9 and Debian 11 link 3.34.1.

    SQLAlchemy withholds its own RETURNING below that version, so turning the
    dialect's three flags off is what one of those machines looks like. An
    explicit clause is not withheld and reaches the database as a syntax error.
    """
    engine.dialect.insert_returning = False
    engine.dialect.update_returning = False
    engine.dialect.delete_returning = False

    with Session(engine) as session, sent_to(engine) as sent:
        with atomic(session):
            opened = stage_clock_in(session, NINE, MONDAY, source=EventSource.USER)
        assert opened is not None
        with atomic(session):
            closed = stage_clock_out(
                session,
                opened.id,
                NINE + timedelta(hours=8),
                source=EventSource.USER,
            )

    assert closed is True
    assert [one for one in sent if "RETURNING" in one.upper()] == []
