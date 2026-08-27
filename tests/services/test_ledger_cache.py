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
