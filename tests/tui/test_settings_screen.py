"""The settings screen writes what it shows, and refuses what it cannot read.

This file used to hold three `SettingsService` round-trips — no screen, no
Pilot — restating tests already in `tests/services/test_settings.py`. The name
reported coverage of a 176-line screen reachable from two places in `app.py`,
and there was none: every branch of `_save` and `_add_next_year` was
unexercised, including the one that drops a year's entitlement when the field
will not parse.
"""

from __future__ import annotations

from pathlib import Path

from textual.pilot import Pilot
from textual.widgets import Button, Input, Select

from flexi.app import FlexiApp
from flexi.constants import Division
from flexi.models.database.app import create_db_engine
from flexi.models.database.db import Base
from flexi.screens.dashboard import DashboardScreen
from flexi.screens.settings import SettingsScreen
from tests.tui.conftest import WIDE, AppFactory, showing


async def open_settings(pilot: Pilot[None]) -> None:
    """`f4` from the dashboard, as somebody would reach it."""
    await pilot.press("f4")
    await pilot.pause()


def stored_start(app: FlexiApp) -> str:
    row = app.services.settings.get_settings()
    assert row is not None
    return row.leave_year_start


# -- getting there ---------------------------------------------------------


async def test_f4_opens_the_settings_screen(app_factory: AppFactory) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_settings(pilot)
        showing(app, SettingsScreen)


async def test_the_fields_arrive_holding_what_is_stored(
    app_factory: AppFactory,
) -> None:
    """A settings screen that opens empty is one that saves an empty setting."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_settings(pilot)
        screen = showing(app, SettingsScreen)
        row = app.services.settings.get_settings()
        assert row is not None

        assert screen.query_one("#input-leave-start", Input).value == (
            row.leave_year_start
        )
        assert screen.query_one("#input-working-days", Input).value == row.working_days
        assert screen.query_one("#input-auto-close", Input).value == row.auto_close_time
        assert screen.query_one("#select-division", Select).value == (
            row.bank_holiday_division
        )


async def test_the_screen_opens_before_any_settings_exist(tmp_path: Path) -> None:
    """`compose` falls back to defaults when there is no row to read.

    Reachable in the application on a database that has been migrated and never
    answered. Every fallback on that path was uncovered.
    """
    path = tmp_path / "empty.db"
    engine = create_db_engine(path)
    Base.metadata.create_all(engine)
    engine.dispose()

    app = FlexiApp(db_path=path)
    async with app.run_test(size=WIDE) as pilot:
        app.push_screen(SettingsScreen(app.services))
        await pilot.pause()
        screen = showing(app, SettingsScreen)
        assert screen.query_one("#input-leave-start", Input).value == "01-01"
        assert screen.query_one("#input-working-days", Input).value == "0,1,2,3,4"
        assert screen.query_one("#input-auto-close", Input).value == "18:00"


# -- saving ----------------------------------------------------------------


async def test_saving_writes_every_field(app_factory: AppFactory) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_settings(pilot)
        screen = showing(app, SettingsScreen)
        screen.query_one("#input-leave-start", Input).value = "04-01"
        screen.query_one("#input-working-days", Input).value = "0,1,2,3"
        screen.query_one("#input-auto-close", Input).value = "17:30"
        screen.query_one("#select-division", Select).value = Division.SCOTLAND.value

        await pilot.click("#btn-save")
        await pilot.pause()

        row = app.services.settings.get_settings()
        assert row is not None
        assert row.leave_year_start == "04-01"
        assert row.working_days == "0,1,2,3"
        assert row.auto_close_time == "17:30"
        assert row.bank_holiday_division == Division.SCOTLAND.value
        showing(app, DashboardScreen)


async def test_an_empty_field_is_refused_and_writes_nothing(
    app_factory: AppFactory,
) -> None:
    """Blanking a field must not blank the setting."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        was = stored_start(app)
        await open_settings(pilot)
        screen = showing(app, SettingsScreen)
        screen.query_one("#input-leave-start", Input).value = ""

        await pilot.click("#btn-save")
        await pilot.pause()

        showing(app, SettingsScreen)  # still open, so the mistake stays visible
        assert stored_start(app) == was


async def test_a_time_that_cannot_be_read_is_refused(app_factory: AppFactory) -> None:
    """`save_settings` raises on a time it cannot parse; the screen must catch it."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_settings(pilot)
        screen = showing(app, SettingsScreen)
        screen.query_one("#input-auto-close", Input).value = "half past six"

        await pilot.click("#btn-save")
        await pilot.pause()

        showing(app, SettingsScreen)
        row = app.services.settings.get_settings()
        assert row is not None
        assert row.auto_close_time != "half past six"


# -- entitlements ----------------------------------------------------------


async def test_a_changed_entitlement_is_written(app_factory: AppFactory) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        year = app.services.settings.active_leave_year()
        app.services.settings.save_entitlement(year, 25.0)

        await open_settings(pilot)
        screen = showing(app, SettingsScreen)
        screen.query_one(f"#ent-{year}", Input).value = "27.5"

        await pilot.click("#btn-save")
        await pilot.pause()

        kept = app.services.settings.get_entitlement(year)
        assert kept is not None
        assert kept.days == 27.5


async def test_an_entitlement_that_is_not_a_number_is_refused(
    app_factory: AppFactory,
) -> None:
    """A year somebody could not type stops the whole save.

    `_save` used to commit the four settings fields first and parse the
    entitlements after, so a rejection left the working pattern and the region
    written, the screen open, and the ledger cache holding figures built
    against the settings that had just been replaced — the application hangs
    `invalidate()` off `dismiss(True)`, and a rejection does not dismiss.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        year = app.services.settings.active_leave_year()
        app.services.settings.save_entitlement(year, 25.0)
        was = stored_start(app)

        await open_settings(pilot)
        screen = showing(app, SettingsScreen)
        screen.query_one("#input-leave-start", Input).value = "07-07"
        screen.query_one(f"#ent-{year}", Input).value = "loads"

        await pilot.click("#btn-save")
        await pilot.pause()

        showing(app, SettingsScreen)
        kept = app.services.settings.get_entitlement(year)
        assert kept is not None
        assert kept.days == 25.0, "the year somebody could not type is left alone"
        assert stored_start(app) == was, "and neither is anything else"


async def test_adding_next_year_carries_this_year_forward(
    app_factory: AppFactory,
) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        year = app.services.settings.active_leave_year()
        app.services.settings.save_entitlement(year, 22.0)

        await open_settings(pilot)
        await pilot.click("#btn-add-year")
        await pilot.pause()

        added = app.services.settings.get_entitlement(year + 1)
        assert added is not None
        assert added.days == 22.0, "next year starts on the same allowance"


async def test_adding_a_year_with_none_on_record_uses_the_default(
    app_factory: AppFactory,
) -> None:
    """The other half of `_add_next_year`, which nothing reached."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        for row in app.services.settings.all_entitlements():
            app.services.session.delete(row)
        app.services.session.commit()

        await open_settings(pilot)
        await pilot.click("#btn-add-year")
        await pilot.pause()

        year = app.services.settings.active_leave_year()
        added = app.services.settings.get_entitlement(year)
        assert added is not None
        assert added.days == 25.0


async def test_adding_a_year_keeps_the_screen_and_everything_typed_into_it(
    app_factory: AppFactory,
) -> None:
    """The button adds a row. It used to leave, taking the form with it.

    `# Refresh screen` described a recompose the code did not perform: it
    dismissed instead, so every field edited above the button was discarded
    unsaved and unmentioned, and dismissing with `True` told the application
    that settings had been changed when only an entitlement had.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_settings(pilot)
        screen = showing(app, SettingsScreen)
        screen.query_one("#input-working-days", Input).value = "Mon-Wed"
        await pilot.pause()

        await pilot.click("#btn-add-year")
        await pilot.pause()

        screen = showing(app, SettingsScreen)
        assert screen.query_one("#input-working-days", Input).value == "Mon-Wed"
        year = app.services.settings.active_leave_year() + 1
        assert screen.query_one(f"#ent-{year}", Input), "the new row should be mounted"


# -- leaving ---------------------------------------------------------------


async def test_back_leaves_everything_as_it_was(app_factory: AppFactory) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        was = stored_start(app)
        await open_settings(pilot)
        screen = showing(app, SettingsScreen)
        screen.query_one("#input-leave-start", Input).value = "09-09"

        await pilot.click("#btn-back")
        await pilot.pause()

        showing(app, DashboardScreen)
        assert stored_start(app) == was


async def test_escape_is_the_same_as_back(app_factory: AppFactory) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_settings(pilot)
        showing(app, SettingsScreen)

        await pilot.press("escape")
        await pilot.pause()

        showing(app, DashboardScreen)


async def test_no_region_selected_is_refused(app_factory: AppFactory) -> None:
    """`Select` can hold `BLANK`, which is not a division and must not be saved."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        was = stored_start(app)
        await open_settings(pilot)
        screen = showing(app, SettingsScreen)
        screen.query_one("#input-leave-start", Input).value = "07-07"
        screen.query_one("#select-division", Select).clear()

        await pilot.click("#btn-save")
        await pilot.pause()

        showing(app, SettingsScreen)
        assert stored_start(app) == was


async def test_a_button_the_screen_does_not_own_does_nothing(
    app_factory: AppFactory,
) -> None:
    """The fallthrough in `on_button_pressed`, which has no key of its own.

    A screen that reacted to any button would react to one mounted by a widget
    it does not control, so the absence of an `else` is the behaviour.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_settings(pilot)
        screen = showing(app, SettingsScreen)

        screen.on_button_pressed(Button.Pressed(Button("Nothing", id="btn-nothing")))
        await pilot.pause()

        showing(app, SettingsScreen), "neither saved nor dismissed"
