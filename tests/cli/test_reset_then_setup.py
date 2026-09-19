"""What happens in the moment after the records are thrown away.

`flexi init` -> Start again deletes the database and then asks the five
questions again. The setup form is a Textual application, and `FlexiApp` builds
an engine and opens a session before a screen is drawn, so the migration has to
run between the delete and the form or the command dies with
`no such table: settings` on a database it has already destroyed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError

import flexi.__main__ as main
from flexi.app import FlexiApp
from flexi.cli import init as init_cli
from flexi.models.database.engine import create_db_engine, get_session
from flexi.models.database.migrate import run_migrations
from flexi.services import setup
from flexi.services.settings import SettingsService, parse_settings

LOCATIONS = (
    "flexi.locations",
    "flexi.__main__",
    "flexi.models.database.engine",
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
        parse_settings(
            leave_year_start="04-06",
            working_days="Mon-Fri",
            bank_holiday_division="england-and-wales",
            auto_close_time="18:00",
        )
    )
    session.close()
    engine.dispose()

    setup.clear_initialisation_cache()
    assert setup.is_initialised(db), "the fixture must start from a set-up machine"

    init_cli.reset(db)
    setup.forget(db)
    return db


def test_records_are_gone(erased: Path) -> None:
    assert not erased.exists()
    assert not setup.is_initialised(erased)


def test_setup_form_opens_after_the_reset(erased: Path) -> None:
    app = main.launch(splash=True)
    try:
        assert app.show_splash, "the first run after a reset earns the animation"
    finally:
        app._session.close()
        app._engine.dispose()


def test_without_the_migration_the_app_cannot_open(erased: Path) -> None:
    """The migration in `launch` is load-bearing, and this is what it prevents.

    The failure comes from ``on_mount``'s first question, not ``__init__``:
    building the registry reads no settings row, because the bank-holiday
    division is asked per query.
    """
    app = FlexiApp()
    try:
        with pytest.raises(OperationalError, match="no such table: settings"):
            app.services.settings.is_setup_complete()
    finally:
        app._session.close()
        app._engine.dispose()


def test_launch_migrates_before_opening_the_app(
    erased: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`launch` is the only place in `__main__` that constructs the application.

    Patched at the source modules: neither name is bound in `__main__`, because
    the application and the migration runner are imported inside the function.
    """
    order: list[str] = []

    def migrated() -> None:
        order.append("migrated")

    def opened() -> _Stub:
        order.append("opened")
        return _Stub()

    monkeypatch.setattr("flexi.models.database.migrate.run_migrations", migrated)
    monkeypatch.setattr("flexi.app.FlexiApp", opened)

    main.launch()

    assert order == ["migrated", "opened"], "the schema must exist before the app"


class _Stub:
    """Stands in for the application, which needs a terminal to be worth building."""

    show_splash = False
    open_settings = False
