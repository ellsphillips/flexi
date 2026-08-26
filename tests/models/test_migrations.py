"""Migrations, forward and back, against a populated database.

`0007` rebuilds `absence_days` rather than altering it, because the v1 schema put
`UNIQUE` on the `date` column itself and SQLite cannot drop a column constraint
in place. A table rebuild that silently loses rows is the kind of bug that is
only discovered by the person whose leave records it ate, so it is checked here.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from flexi.constants import AbsenceType, Portion
from flexi.locations import backups_directory, ensure
from flexi.models.database.db import AbsenceDay, Base, Settings
from flexi.models.database.engine import create_db_engine, get_session
from flexi.models.database.migrate import HEAD as RECORDED_HEAD
from flexi.models.database.migrate import (
    MAX_BACKUPS,
    alembic_config,
    run_migrations,
)

BEFORE_HALF_DAYS = "0006"
HEAD = "head"


def test_the_recorded_head_is_the_head_alembic_would_find(db: Path) -> None:
    """`run_migrations` compares against a written-down revision to stay fast.

    Asking Alembic costs the import this exists to avoid, so the number is
    duplicated -- and a duplicate nobody checks is a duplicate that drifts. Add
    a migration without touching `HEAD` and every database silently reports
    itself up to date, which is a schema change that never runs.
    """
    with alembic_config(db) as cfg:
        assert ScriptDirectory.from_config(cfg).get_current_head() == RECORDED_HEAD


def test_a_file_that_was_never_migrated_is_migrated_rather_than_refused(
    db: Path,
) -> None:
    """An interrupted first run leaves a database with no `alembic_version`.

    The cheap revision check queries that table directly, so the case has to
    read as "never migrated" rather than raise -- otherwise the one file that
    needs the migration most is the one that cannot get it.
    """
    db.touch()

    run_migrations(db)

    assert revision_of(db) == RECORDED_HEAD


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "flexi.db"


def upgrade(db: Path, revision: str) -> None:
    with alembic_config(db) as cfg:
        command.upgrade(cfg, revision)


def downgrade(db: Path, revision: str) -> None:
    with alembic_config(db) as cfg:
        command.downgrade(cfg, revision)


def revision_of(db: Path) -> str:
    """The schema version stamped on a database file, read without Alembic."""
    connection = sqlite3.connect(f"{db.absolute().as_uri()}?mode=ro", uri=True)
    try:
        stamped = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
    finally:
        connection.close()
    return str(stamped[0])


def rows(db: Path, table: str) -> list[tuple[object, ...]]:
    engine = create_db_engine(db)
    try:
        with engine.connect() as connection:
            # S608: the table name comes from this module, never from input.
            statement = sa.text(f"SELECT * FROM {table}")  # noqa: S608
            return [tuple(row) for row in connection.execute(statement)]
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


def test_upgrading_an_existing_database_snapshots_it_as_it_was(db: Path) -> None:
    """The copy has to be of the old schema, or it is no way back.

    A backup taken after the upgrade would be indistinguishable from the file it
    was meant to rescue. What is checked here is the stamp: the snapshot beside
    the database says 0006, so restoring it undoes the migration rather than
    reinstating it.
    """
    upgrade(db, BEFORE_HALF_DAYS)

    run_migrations(db)

    snapshots = list(backups_directory().glob("*.bak"))
    assert len(snapshots) == 1
    assert revision_of(snapshots[0]) == BEFORE_HALF_DAYS
    with alembic_config(db) as cfg:
        head = ScriptDirectory.from_config(cfg).get_current_head()
    assert revision_of(db) == head


def test_the_backup_an_upgrade_takes_ages_out_the_oldest_one(db: Path) -> None:
    """Ten is the whole allowance, and an upgrade is what fills it.

    One backup per migration, and Flexi migrates whenever it starts on a new
    version. Without housekeeping in the same breath as the copy, an ordinary
    fortnight of upgrades leaves a data directory that only ever grows.
    """
    upgrade(db, BEFORE_HALF_DAYS)
    directory = ensure(backups_directory())
    for n in range(MAX_BACKUPS):
        earlier = directory / f"flexi_2026{n:04d}T000000Z.bak"
        earlier.write_bytes(b"an earlier upgrade")
        os.utime(earlier, (1_000_000 + n, 1_000_000 + n))
    oldest = directory / "flexi_20260000T000000Z.bak"

    run_migrations(db)

    assert len(list(directory.glob("*.bak"))) == MAX_BACKUPS
    assert not oldest.exists(), "the newest snapshot did not age out the oldest"


def test_the_migrations_build_the_schema_the_models_describe(db: Path) -> None:
    """What real users get, compared against what every fixture gets.

    Fixtures call `Base.metadata.create_all`; a person who installs Flexi gets
    `run_migrations`. Nothing compared the two, so a model changed without a
    migration would pass the whole suite and fail on the first real launch --
    and a migration that drifted from the models would do the reverse.

    Server defaults are compared too. Alembic leaves that off by default, and
    with it off the guard was blind to the one axis the two schemas disagreed
    on: `clock_events.source` and `work_sessions.auto_closed` are `DEFAULT`ed by
    migration 0004 and were not by the models, so `--demo` and all ten fixture
    databases ran against a schema no real install has.
    """
    upgrade(db, HEAD)

    engine = create_db_engine(db)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection, opts={"compare_server_default": True}
            )
            differences = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    # Alembic reports the version table it owns; the models do not describe it.
    real = [
        difference
        for difference in differences
        if "alembic_version" not in str(difference)
    ]
    assert real == [], f"the migrations and the models disagree: {real}"
