"""What happens in the moment after the records are thrown away.

`flexi init` -> Start again deletes the database and then asks the five
questions again. The setup form is a Textual application, and `App.__init__`
builds an engine, opens a session and reads the settings row before a single
screen is drawn -- so between the delete and the form there has to be a
migration, or the command dies with `no such table: settings` having already
destroyed everything it was asked to destroy.

It shipped like that. `_ask_the_questions` opened the application and left every
caller to have migrated first; there were four callers and the reset path,
added last, did not. The invariant is now established where it is needed rather
than asserted in the places that happen to remember.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError

import flexi.__main__ as main
from flexi.app import App
from flexi.cli import init as init_cli
from flexi.models.database.app import create_db_engine, get_session
from flexi.models.database.migrate import run_migrations
from flexi.services import setup
from flexi.services.settings import SettingsService

LOCATIONS = (
    "flexi.locations",
    "flexi.__main__",
    "flexi.models.database.app",
    "flexi.models.database.migrate",
    "flexi.services.setup",
)


@pytest.fixture
def erased(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A machine that was set up, and has just had Start again chosen on it."""
    db = tmp_path / "db.db"
    backups = tmp_path / "backups"
    for module in LOCATIONS:
        monkeypatch.setattr(f"{module}.database_file", lambda: db)
    for module in ("flexi.models.database.backup", "flexi.models.database.migrate"):
        monkeypatch.setattr(f"{module}.backups_directory", lambda: backups)

    run_migrations(db)
    engine = create_db_engine(db)
    session = get_session(engine)
    SettingsService(session).save_settings(
        leave_year_start="04-06",
        working_days="Mon-Fri",
        bank_holiday_division="england-and-wales",
        auto_close_time="18:00",
    )
    session.close()
    engine.dispose()

    setup._INITIALISED.clear()
    assert setup.is_initialised(db), "the fixture must start from a set-up machine"

    init_cli.reset(db)
    setup.forget(db)
    return db


def test_the_records_really_are_gone(erased: Path) -> None:
    assert not erased.exists()
    assert not setup.is_initialised(erased)


def test_the_setup_form_opens_after_the_records_are_erased(erased: Path) -> None:
    """The crash somebody hit: erase, then straight into the five questions."""
    app = main._launch(splash=True)
    try:
        assert app.show_splash, "the first run after a reset earns the animation"
    finally:
        app._session.close()
        app._engine.dispose()


def test_the_migration_in_launch_is_what_makes_that_work(erased: Path) -> None:
    """The guard is load-bearing rather than belt-and-braces.

    Without it this is the exact traceback, thrown after the database has
    already been deleted and with the snapshot the only thing standing between
    somebody and the loss of a year of records.
    """
    with pytest.raises(OperationalError, match="no such table: settings"):
        App()


def test_every_way_into_the_application_migrates_first(
    erased: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Four callers, one forgot. Assert the seam rather than the callers.

    `_launch` is the only place in `__main__` that constructs the application,
    so this is the one place the invariant has to hold.
    """
    order: list[str] = []

    def migrated() -> None:
        order.append("migrated")

    def opened() -> _Stub:
        order.append("opened")
        return _Stub()

    monkeypatch.setattr(main, "run_migrations", migrated)
    monkeypatch.setattr(main, "App", opened)

    main._launch()

    assert order == ["migrated", "opened"], "the schema must exist before App is built"


class _Stub:
    """Stands in for the application, which needs a terminal to be worth building."""

    show_splash = False
    open_settings = False
