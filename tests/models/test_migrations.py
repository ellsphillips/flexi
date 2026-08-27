"""Migrations, forward and back, against a populated database.

`0007` rebuilds `absence_days` rather than altering it, because the v1 schema put
`UNIQUE` on the `date` column itself and SQLite cannot drop a column constraint
in place. A table rebuild that silently loses rows is the kind of bug that is
only discovered by the person whose leave records it ate, so it is checked here.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.exc import DatabaseError

from flexi.constants import AbsenceType, Portion
from flexi.locations import backups_directory, ensure
from flexi.models.database.db import (
    AbsenceDay,
    BankHolidayCache,
    BankHolidayRefresh,
    Base,
    Settings,
    WorkSession,
)
from flexi.models.database.engine import create_db_engine, database_scope, get_session
from flexi.models.database.lease import DatabaseBusyError
from flexi.models.database.migrate import HEAD as RECORDED_HEAD
from flexi.models.database.migrate import (
    MAX_BACKUPS,
    DatabaseRevision,
    RevisionState,
    alembic_config,
    current_revision,
    run_migrations,
)

BEFORE_HALF_DAYS = "0006"
BEFORE_INVARIANTS = "0010"
BEFORE_BANK_HOLIDAY_REFRESHES = "0012"
HEAD = "head"


def test_revision_result_rejects_contradictory_states() -> None:
    """A caller cannot manufacture a result whose state disagrees with its data."""
    with pytest.raises(ValueError, match="must carry a revision"):
        DatabaseRevision(RevisionState.STAMPED)
    with pytest.raises(ValueError, match="cannot carry a revision"):
        DatabaseRevision(RevisionState.ABSENT, "0001")


def test_revision_inspection_distinguishes_missing_and_empty_databases(
    db: Path,
) -> None:
    assert current_revision(db) == DatabaseRevision(RevisionState.ABSENT)

    sqlite3.connect(db).close()

    assert current_revision(db) == DatabaseRevision(RevisionState.EMPTY)


@pytest.mark.parametrize(
    "schema",
    [
        "CREATE TABLE records (id INTEGER PRIMARY KEY)",
        "CREATE VIEW records AS SELECT 1 AS id",
    ],
)
def test_revision_inspection_identifies_an_unstamped_schema(
    db: Path, schema: str
) -> None:
    with sqlite3.connect(db) as connection:
        connection.execute(schema)

    assert current_revision(db) == DatabaseRevision(RevisionState.UNSTAMPED)


def test_revision_inspection_identifies_an_empty_stamp_table(db: Path) -> None:
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num TEXT)")

    assert current_revision(db) == DatabaseRevision(RevisionState.UNSTAMPED)


def test_revision_inspection_carries_the_database_stamp(db: Path) -> None:
    upgrade(db, BEFORE_HALF_DAYS)

    assert current_revision(db) == DatabaseRevision(
        RevisionState.STAMPED, BEFORE_HALF_DAYS
    )


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        (("0001", "0002"), "multiple migration revisions"),
        ((None,), "invalid migration revision"),
    ],
)
def test_revision_inspection_refuses_ambiguous_stamps(
    db: Path, rows: tuple[str | None, ...], message: str
) -> None:
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num TEXT)")
        connection.executemany(
            "INSERT INTO alembic_version VALUES (?)", ((row,) for row in rows)
        )

    with pytest.raises(RuntimeError, match=message):
        current_revision(db)


def test_a_corrupt_database_is_not_mistaken_for_a_fresh_one(db: Path) -> None:
    db.write_bytes(b"not a sqlite database")

    with pytest.raises(DatabaseError, match="not a database"):
        run_migrations(db)

    assert db.read_bytes() == b"not a sqlite database"


def test_an_unstamped_existing_schema_is_not_assumed_to_belong_to_flexi(
    db: Path,
) -> None:
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE somebody_elses_data (value TEXT)")
        connection.execute("INSERT INTO somebody_elses_data VALUES ('kept')")

    with pytest.raises(RuntimeError, match="unstamped schema"):
        run_migrations(db)

    with sqlite3.connect(db) as connection:
        assert connection.execute(
            "SELECT value FROM somebody_elses_data"
        ).fetchone() == ("kept",)


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
    """A schema-empty file is fresh even though the filesystem entry exists.

    It has nothing for a recovery copy to protect, so it follows the same path
    as an absent file rather than the unsafe unstamped-schema path.
    """
    db.touch()

    run_migrations(db)

    assert revision_of(db) == RECORDED_HEAD
    assert not list(backups_directory().glob("*.bak"))


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


def test_bank_holiday_refresh_metadata_is_backfilled(db: Path) -> None:
    """Each legacy division keeps its latest complete-cache timestamp."""
    upgrade(db, BEFORE_BANK_HOLIDAY_REFRESHES)
    engine = create_db_engine(db)
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO bank_holiday_cache"
                " (id, division, date, title, fetched_at) VALUES"
                " (1, 'england-and-wales', '2026-01-01', 'New Year',"
                "  '2026-01-02 09:00:00'),"
                " (2, 'england-and-wales', '2026-12-25', 'Christmas',"
                "  '2026-01-03 09:00:00'),"
                " (3, 'scotland', '2026-11-30', 'St Andrew',"
                "  '2026-02-01 10:30:00')"
            )
        )
        connection.commit()
    engine.dispose()

    upgrade(db, HEAD)

    engine = create_db_engine(db)
    session = get_session(engine)
    try:
        refreshes = session.query(BankHolidayRefresh).order_by(
            BankHolidayRefresh.division
        )
        assert [(refresh.division, refresh.fetched_at) for refresh in refreshes] == [
            ("england-and-wales", datetime(2026, 1, 3, 9, 0)),
            ("scotland", datetime(2026, 2, 1, 10, 30)),
        ]
        assert session.query(BankHolidayCache).count() == 3
        assert "fetched_at" not in {
            column["name"]
            for column in sa.inspect(engine).get_columns("bank_holiday_cache")
        }
    finally:
        session.close()
        engine.dispose()


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


def test_valid_legacy_states_survive_the_invariant_upgrade(db: Path) -> None:
    """0011 adds enforcement without rewriting any valid user record."""
    upgrade(db, BEFORE_INVARIANTS)
    engine = create_db_engine(db)
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO settings"
                " (id, leave_year_start, working_days, bank_holiday_division,"
                "  auto_close_time)"
                " VALUES (41, '04-06', '0,1,2,3,4',"
                " 'england-and-wales', '18:00')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO clock_events"
                " (id, action, timestamp, source, utc_offset_minutes)"
                " VALUES (51, 'IN', '2026-06-10 09:00:00', 'user', 0)"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO work_sessions"
                " (id, clock_in_id, clock_out_id, work_date, auto_closed, voided)"
                " VALUES (61, 51, NULL, '2026-06-10', 0, 0)"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO absence_days (id, date, absence_type, portion)"
                " VALUES"
                " (71, '2026-06-11', 'SICK', 'AM'),"
                " (72, '2026-06-11', 'ANNUAL', 'PM')"
            )
        )
        connection.commit()
    engine.dispose()

    upgrade(db, HEAD)

    session = get_session(create_db_engine(db))
    try:
        settings = session.query(Settings).one()
        assert settings.id == 41
        assert settings.singleton_key == 1
        assert session.query(WorkSession).one().id == 61
        assert [
            item.id for item in session.query(AbsenceDay).order_by(AbsenceDay.id)
        ] == [
            71,
            72,
        ]
    finally:
        session.close()


@pytest.mark.parametrize(
    ("statements", "expected"),
    [
        (
            (
                "INSERT INTO settings"
                " (id, leave_year_start, working_days, bank_holiday_division,"
                " auto_close_time) VALUES"
                " (1, '01-01', '0,1,2,3,4', 'england-and-wales', '18:00'),"
                " (2, '01-01', '0,1,2,3,4', 'england-and-wales', '18:00')",
            ),
            "settings has 2 rows",
        ),
        (
            (
                "INSERT INTO clock_events"
                " (id, action, timestamp, source, utc_offset_minutes) VALUES"
                " (1, 'IN', '2026-06-10 09:00:00', 'user', 0),"
                " (2, 'IN', '2026-06-10 10:00:00', 'user', 0)",
                "INSERT INTO work_sessions"
                " (id, clock_in_id, clock_out_id, work_date, auto_closed, voided)"
                " VALUES"
                " (1, 1, NULL, '2026-06-10', 0, 0),"
                " (2, 2, NULL, '2026-06-10', 0, 0)",
            ),
            "work_sessions has 2 non-voided open rows",
        ),
        (
            (
                "INSERT INTO absence_days (id, date, absence_type, portion)"
                " VALUES"
                " (1, '2026-06-10', 'ANNUAL', 'FULL'),"
                " (2, '2026-06-10', 'SICK', 'AM')",
            ),
            "absence_days mixes FULL with a half on: 2026-06-10",
        ),
    ],
)
def test_ambiguous_legacy_states_fail_before_the_schema_changes(
    db: Path,
    statements: tuple[str, ...],
    expected: str,
) -> None:
    """The migration names records a person must resolve instead of choosing."""
    upgrade(db, BEFORE_INVARIANTS)
    engine = create_db_engine(db)
    with engine.connect() as connection:
        for statement in statements:
            connection.execute(sa.text(statement))
        connection.commit()
    engine.dispose()

    with pytest.raises(RuntimeError, match=expected):
        upgrade(db, HEAD)

    assert revision_of(db) == BEFORE_INVARIANTS


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


def test_a_migration_refuses_an_application_using_the_old_schema(db: Path) -> None:
    """DDL cannot run beneath an engine whose mappings assume the old schema."""
    upgrade(db, BEFORE_HALF_DAYS)

    with (
        database_scope(db),
        pytest.raises(DatabaseBusyError, match="in use"),
    ):
        run_migrations(db)

    assert revision_of(db) == BEFORE_HALF_DAYS
    run_migrations(db)
    assert revision_of(db) == RECORDED_HEAD


def test_an_upgrade_refuses_a_backup_that_does_not_verify(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Alembic never sees a stamped database without a proven way back."""
    upgrade(db, BEFORE_HALF_DAYS)
    monkeypatch.setattr("flexi.models.database.migrate.verify", lambda _path: False)

    with pytest.raises(RuntimeError, match="backup did not verify"):
        run_migrations(db)

    assert revision_of(db) == BEFORE_HALF_DAYS
    assert len(list(backups_directory().glob("*.bak"))) == 1


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
