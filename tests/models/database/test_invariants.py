"""Database invariants shared by metadata-created and migrated schemas."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Literal, cast

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from flexi.constants import AbsenceType, ClockAction, Portion
from flexi.models.database.db import (
    SETTINGS_SINGLETON_KEY,
    AbsenceDay,
    Base,
    ClockEvent,
    Settings,
    WorkSession,
)
from flexi.models.database.engine import create_db_engine, get_session
from flexi.models.database.migrate import run_migrations

SchemaSource = Literal["metadata", "migrations"]


@pytest.fixture(params=("metadata", "migrations"))
def invariant_engine(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[Engine]:
    """Build each test database through both supported schema paths."""
    source = cast("SchemaSource", request.param)
    path = tmp_path / f"{source}.db"
    if source == "migrations":
        run_migrations(path)
    engine = create_db_engine(path)
    if source == "metadata":
        Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def settings_row(*, singleton_key: int = SETTINGS_SINGLETON_KEY) -> Settings:
    """Return a complete settings row without relying on its service."""
    return Settings(
        leave_year_start="01-01",
        working_days="0,1,2,3,4",
        bank_holiday_division="england-and-wales",
        auto_close_time="18:00",
        singleton_key=singleton_key,
    )


def add_open_session(session: Session, *, voided: bool = False) -> WorkSession:
    """Insert an open session directly, bypassing clock-service checks."""
    event = ClockEvent(
        action=ClockAction.IN,
        timestamp=datetime(2026, 6, 10, 9),
        utc_offset_minutes=0,
    )
    session.add(event)
    session.flush()
    work_session = WorkSession(
        clock_in_id=event.id,
        work_date=date(2026, 6, 10),
        voided=voided,
    )
    session.add(work_session)
    return work_session


@pytest.mark.parametrize("second_key", [SETTINGS_SINGLETON_KEY, 2])
def test_settings_is_a_database_singleton(
    invariant_engine: Engine, second_key: int
) -> None:
    """Neither the canonical nor an invented key can create a second row."""
    with (
        get_session(invariant_engine) as first,
        get_session(invariant_engine) as second,
    ):
        first.add(settings_row())
        second.add(settings_row(singleton_key=second_key))

        first.commit()
        with pytest.raises(IntegrityError):
            second.commit()
        second.rollback()

    with get_session(invariant_engine) as session:
        assert session.scalar(select(func.count()).select_from(Settings)) == 1


def test_only_one_non_voided_session_can_be_open(invariant_engine: Engine) -> None:
    """The second of two independently prepared open sessions loses atomically."""
    with get_session(invariant_engine) as seed:
        events = (
            ClockEvent(
                action=ClockAction.IN,
                timestamp=datetime(2026, 6, 10, 9),
                utc_offset_minutes=0,
            ),
            ClockEvent(
                action=ClockAction.IN,
                timestamp=datetime(2026, 6, 10, 10),
                utc_offset_minutes=0,
            ),
        )
        seed.add_all(events)
        seed.commit()
        event_ids = tuple(event.id for event in events)

    with (
        get_session(invariant_engine) as first,
        get_session(invariant_engine) as second,
    ):
        first.add(WorkSession(clock_in_id=event_ids[0], work_date=date(2026, 6, 10)))
        second.add(WorkSession(clock_in_id=event_ids[1], work_date=date(2026, 6, 10)))

        first.commit()
        with pytest.raises(IntegrityError):
            second.commit()
        second.rollback()

    with get_session(invariant_engine) as session:
        assert session.scalar(select(func.count()).select_from(WorkSession)) == 1


def test_voided_open_sessions_do_not_occupy_the_live_slot(
    invariant_engine: Engine,
) -> None:
    """Historical incomplete rows remain representable beside the live session."""
    with get_session(invariant_engine) as session:
        add_open_session(session, voided=True)
        add_open_session(session)
        session.commit()

        assert session.scalar(select(func.count()).select_from(WorkSession)) == 2


def test_am_and_pm_can_share_a_date(invariant_engine: Engine) -> None:
    """The exclusion indexes retain the valid two-half-day case."""
    booked = date(2026, 6, 10)
    with get_session(invariant_engine) as session:
        session.add_all(
            (
                AbsenceDay(
                    date=booked,
                    absence_type=AbsenceType.SICK,
                    portion=Portion.AM,
                ),
                AbsenceDay(
                    date=booked,
                    absence_type=AbsenceType.ANNUAL,
                    portion=Portion.PM,
                ),
            )
        )
        session.commit()

        assert session.scalar(select(func.count()).select_from(AbsenceDay)) == 2


@pytest.mark.parametrize("half", [Portion.AM, Portion.PM])
@pytest.mark.parametrize("full_first", [False, True], ids=("half-first", "full-first"))
def test_full_and_half_days_are_mutually_exclusive(
    invariant_engine: Engine,
    half: Portion,
    *,
    full_first: bool,
) -> None:
    """Both partial indexes reject collisions in either insertion order."""
    booked = date(2026, 6, 10)
    first_portion, second_portion = (
        (Portion.FULL, half) if full_first else (half, Portion.FULL)
    )

    with (
        get_session(invariant_engine) as first,
        get_session(invariant_engine) as second,
    ):
        first.add(
            AbsenceDay(
                date=booked,
                absence_type=AbsenceType.ANNUAL,
                portion=first_portion,
            )
        )
        second.add(
            AbsenceDay(
                date=booked,
                absence_type=AbsenceType.SICK,
                portion=second_portion,
            )
        )

        first.commit()
        with pytest.raises(IntegrityError):
            second.commit()
        second.rollback()
