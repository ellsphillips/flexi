"""Migrations, forward and back, against a populated database.

`0007` rebuilds `absence_days` rather than altering it, because the v1 schema put
`UNIQUE` on the `date` column itself and SQLite cannot drop a column constraint
in place. A table rebuild that silently loses rows is the kind of bug that is
only discovered by the person whose leave records it ate, so it is checked here.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command

from flexi.models.database.app import create_db_engine, get_session
from flexi.models.database.migrate import _get_alembic_config
from flexi.models.database.db import AbsenceDay, Settings
from flexi.constants import AbsenceType, Portion

BEFORE_HALF_DAYS = "0006"
HEAD = "head"


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    return tmp_path / "flexi.db"


def upgrade(db: Path, revision: str) -> None:
    command.upgrade(_get_alembic_config(db), revision)


def downgrade(db: Path, revision: str) -> None:
    command.downgrade(_get_alembic_config(db), revision)


def rows(db: Path, table: str) -> list[tuple[object, ...]]:
    engine = create_db_engine(db)
    try:
        with engine.connect() as connection:
            return [tuple(row) for row in connection.execute(sa.text(f"SELECT * FROM {table}"))]
    finally:
        engine.dispose()


def test_a_fresh_database_reaches_head(db: Path) -> None:
    """It builds the whole schema from nothing."""
    upgrade(db, HEAD)
    engine = create_db_engine(db)
    try:
        names = set(sa.inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert {"settings", "clock_events", "work_sessions", "absence_days"} <= names


def test_existing_absences_survive_the_rebuild(db: Path) -> None:
    """It carries every v1 row across, as a full day, which is what it was."""
    upgrade(db, BEFORE_HALF_DAYS)
    engine = create_db_engine(db)
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO absence_days (id, date, absence_type)"
                " VALUES (1, '2026-06-10', 'ANNUAL'), (2, '2026-06-11', 'SICK')"
            )
        )
        connection.commit()
    engine.dispose()

    upgrade(db, HEAD)

    session = get_session(create_db_engine(db))
    try:
        booked = session.query(AbsenceDay).order_by(AbsenceDay.date).all()
        assert [item.date for item in booked] == [date(2026, 6, 10), date(2026, 6, 11)]
        assert all(item.portion is Portion.FULL for item in booked)
        assert booked[0].absence_type is AbsenceType.ANNUAL
    finally:
        session.close()


def test_the_new_columns_are_backfilled(db: Path) -> None:
    """It gives an existing settings row the contracted day the code assumed."""
    upgrade(db, "0005")
    engine = create_db_engine(db)
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO settings"
                " (id, leave_year_start, working_days, bank_holiday_division,"
                "  auto_close_time)"
                " VALUES (1, '04-06', '0,1,2,3,4', 'england-and-wales', '18:00')"
            )
        )
        connection.commit()
    engine.dispose()

    upgrade(db, HEAD)

    session = get_session(create_db_engine(db))
    try:
        settings = session.query(Settings).one()
        assert settings.contracted_minutes == 444
        assert settings.day_window_start == "07:00"
        assert settings.day_window_end == "19:00"
    finally:
        session.close()


def test_half_days_of_different_types_share_a_date(db: Path) -> None:
    """It moves uniqueness from the date to the pair, which is the point of 0007."""
    upgrade(db, HEAD)
    session = get_session(create_db_engine(db))
    try:
        session.add_all(
            [
                AbsenceDay(
                    date=date(2026, 6, 10),
                    absence_type=AbsenceType.SICK,
                    portion=Portion.AM,
                ),
                AbsenceDay(
                    date=date(2026, 6, 10),
                    absence_type=AbsenceType.ANNUAL,
                    portion=Portion.PM,
                ),
            ]
        )
        session.commit()
        assert session.query(AbsenceDay).count() == 2
    finally:
        session.close()


def test_a_second_booking_of_the_same_portion_is_refused_by_the_database(
    db: Path,
) -> None:
    """The constraint is real, not just an application rule."""
    upgrade(db, HEAD)
    session = get_session(create_db_engine(db))
    try:
        session.add(
            AbsenceDay(
                date=date(2026, 6, 10),
                absence_type=AbsenceType.SICK,
                portion=Portion.AM,
            )
        )
        session.commit()
        session.add(
            AbsenceDay(
                date=date(2026, 6, 10),
                absence_type=AbsenceType.ANNUAL,
                portion=Portion.AM,
            )
        )
        with pytest.raises(sa.exc.IntegrityError):
            session.commit()
    finally:
        session.rollback()
        session.close()


def test_work_sessions_keep_their_events_across_the_upgrade(db: Path) -> None:
    """It does not touch the tables it did not mean to."""
    upgrade(db, BEFORE_HALF_DAYS)
    # Raw SQL, not the ORM: the model has `note` and `voided`, and 0008 is what
    # adds them. Writing through the model here would be testing the schema
    # against itself rather than against what is on disk.
    engine = create_db_engine(db)
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO clock_events (id, action, timestamp, source)"
                " VALUES (1, 'IN', '2026-06-10 09:00:00', 'user')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO work_sessions"
                " (id, clock_in_id, clock_out_id, work_date, auto_closed)"
                " VALUES (1, 1, NULL, '2026-06-10', 0)"
            )
        )
        connection.commit()
    engine.dispose()

    upgrade(db, HEAD)
    assert len(rows(db, "work_sessions")) == 1
    assert len(rows(db, "clock_events")) == 1


def test_head_downgrades_and_upgrades_again(db: Path) -> None:
    """A downgrade is a deliberate act, and it has to be survivable.

    Half days and the two new types have nowhere to go in the v1 schema, so 0007
    drops them rather than coercing a sick morning into a whole day off. What is
    representable comes back.
    """
    upgrade(db, HEAD)
    session = get_session(create_db_engine(db))
    try:
        session.add_all(
            [
                AbsenceDay(
                    date=date(2026, 6, 10),
                    absence_type=AbsenceType.ANNUAL,
                    portion=Portion.FULL,
                ),
                AbsenceDay(
                    date=date(2026, 6, 11),
                    absence_type=AbsenceType.SICK,
                    portion=Portion.AM,
                ),
                AbsenceDay(
                    date=date(2026, 6, 12),
                    absence_type=AbsenceType.UNPAID,
                    portion=Portion.FULL,
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    downgrade(db, BEFORE_HALF_DAYS)
    surviving = rows(db, "absence_days")
    assert len(surviving) == 1, "only the representable full day should remain"

    upgrade(db, HEAD)
    session = get_session(create_db_engine(db))
    try:
        assert session.query(AbsenceDay).count() == 1
    finally:
        session.close()
