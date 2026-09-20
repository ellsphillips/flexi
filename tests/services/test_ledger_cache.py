"""A ledger cache follows commits from every database connection."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
import time_machine
from pytest_mock import MockerFixture
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from flexi.constants import AbsenceType
from flexi.models.database.engine import get_session
from flexi.services.ledger import ledger_revision
from flexi.services.registry import build_services
from flexi.services.settings import parse_settings
from tests.services.conftest import Configured

MONDAY = date(2026, 8, 10)
AFTER_WEEK = datetime(2026, 8, 17, 12, tzinfo=UTC)


def test_an_external_commit_invalidates_a_cached_historical_day(
    configure: Configured,
    engine: Engine,
) -> None:
    services = configure()

    with time_machine.travel(AFTER_WEEK, tick=False):
        before = services.ledger.day(MONDAY)
        assert before.absences == ()

        with get_session(engine) as competing_session:
            competing = build_services(competing_session)
            assert competing.absence.book(MONDAY, AbsenceType.SICK).success

        after = services.ledger.day(MONDAY)

    assert after is not before
    assert len(after.absences) == 1
    assert after.expected == timedelta()


def test_rollback_cannot_carry_a_cache_onto_an_unrelated_connection(
    configure: Configured,
    session: Session,
    engine: Engine,
) -> None:
    services = configure()
    with time_machine.travel(AFTER_WEEK, tick=False):
        before = services.ledger.day(MONDAY)

        with get_session(engine) as competing_session:
            competing = build_services(competing_session)
            competing.adjustments.record(MONDAY, timedelta(hours=1), "external")
        session.rollback()

        after = services.ledger.day(MONDAY)

    assert after is not before
    assert after.adjustment == timedelta(hours=1)


def test_the_revision_names_its_connection_and_external_counter(
    session: Session,
    engine: Engine,
) -> None:
    before = ledger_revision(session)

    with get_session(engine) as competing:
        competing.execute(text("CREATE TABLE revision_probe (id)"))
        competing.commit()

    after = ledger_revision(session)

    assert after.connection is before.connection
    assert after.data_version > before.data_version


def test_a_revision_requires_an_active_database_driver(
    session: Session,
    mocker: MockerFixture,
) -> None:
    connection = mocker.MagicMock()
    connection.connection.dbapi_connection = None
    mocker.patch.object(session, "connection", return_value=connection)

    with pytest.raises(RuntimeError, match="active database connection"):
        ledger_revision(session)


def test_a_revision_rejects_a_non_integer_sqlite_counter(
    session: Session,
    mocker: MockerFixture,
) -> None:
    connection = mocker.MagicMock()
    connection.connection.dbapi_connection = object()
    connection.exec_driver_sql.return_value.scalar_one.return_value = "one"
    mocker.patch.object(session, "connection", return_value=connection)

    with pytest.raises(TypeError, match="invalid data_version"):
        ledger_revision(session)


def test_an_unrelated_transaction_cannot_clear_the_cache(
    configure: Configured,
    engine: Engine,
) -> None:
    services = configure()
    with time_machine.travel(AFTER_WEEK, tick=False):
        before = services.ledger.day(MONDAY)
        with get_session(engine) as unrelated:
            services.ledger.invalidate_after_transaction(unrelated)
        after = services.ledger.day(MONDAY)

    assert after is before


def test_external_settings_changes_refresh_retained_orm_rows(
    configure: Configured, engine: Engine
) -> None:
    services = configure()
    retained = services.settings.get_settings()
    assert retained is not None
    before = services.ledger.day(MONDAY, now=AFTER_WEEK)

    with get_session(engine) as competing:
        build_services(competing).settings.save_settings(
            parse_settings(
                leave_year_start="10-20",
                working_days="0,1,2,3,4",
                bank_holiday_division="england-and-wales",
                auto_close_time="18:00",
                contracted_minutes=300,
            )
        )

    after = services.ledger.day(MONDAY, now=AFTER_WEEK)

    assert after.expected == timedelta(hours=5)
    assert after.expected != before.expected
    assert retained.contracted_minutes == 300


def test_external_clock_out_refreshes_a_retained_work_session(
    configure: Configured, engine: Engine
) -> None:
    services = configure()
    result = services.clock.clock_in(now=datetime(2026, 8, 10, 9, tzinfo=UTC))
    assert result.session is not None
    assert services.ledger.day(MONDAY, now=AFTER_WEEK).is_open

    with get_session(engine) as competing:
        assert (
            build_services(competing)
            .clock.clock_out(now=datetime(2026, 8, 10, 17, tzinfo=UTC))
            .success
        )

    after = services.ledger.day(MONDAY, now=AFTER_WEEK)

    assert not after.is_open
    assert after.worked == timedelta(hours=8)
    assert result.session.clock_out_id is not None


def test_refresh_keeps_pending_settings_and_session_edits_rollbackable(
    configure: Configured, engine: Engine, session: Session
) -> None:
    services = configure()
    services.clock.clock_in(now=datetime(2026, 8, 10, 9, tzinfo=UTC))
    closed = services.clock.clock_out(now=datetime(2026, 8, 10, 17, tzinfo=UTC))
    assert closed.session is not None
    retained = services.settings.get_settings()
    assert retained is not None
    services.ledger.day(MONDAY, now=AFTER_WEEK)
    retained.contracted_minutes = 300
    closed.session.note = "Pending note"

    with get_session(engine) as competing:
        build_services(competing).adjustments.record(
            MONDAY, timedelta(hours=1), "external"
        )

    after = services.ledger.day(MONDAY, now=AFTER_WEEK)

    assert after.expected == timedelta(hours=5)
    assert after.segments[0].note == "Pending note"
    session.rollback()
    assert retained.contracted_minutes == 444
    assert closed.session.note is None
