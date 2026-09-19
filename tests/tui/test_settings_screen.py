"""The settings screen writes what it shows, and refuses what it cannot read.

Driven through `Pilot`, because `_save` and `_add_next_year` are screen
branches: the service round-trips underneath them are covered by
`tests/services/test_settings.py`.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from textual.pilot import Pilot
from textual.widgets import Button, Input, Select

from flexi.app import FlexiApp
from flexi.components.yearcalendar import YearCalendar
from flexi.constants import Division
from flexi.models.database.db import Base
from flexi.models.database.engine import create_db_engine
from flexi.screens.dashboard import DashboardScreen
from flexi.screens.leave import LeaveScreen
from flexi.screens.settings import SettingsScreen, describe_working_days
from flexi.services.settings import parse_working_days
from tests.tui.conftest import WIDE, AppFactory, screen_text, showing


async def open_settings(pilot: Pilot[None]) -> None:
    """Reach the settings screen with `f4` from the dashboard."""
    await pilot.press("f4")
    await pilot.pause()


def stored_start(app: FlexiApp) -> str:
    row = app.services.settings.get_settings()
    assert row is not None
    return row.leave_year_start


# getting there


async def test_f4_opens_the_settings_screen(app_factory: AppFactory) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_settings(pilot)
        showing(app, SettingsScreen)


async def test_fields_arrive_holding_what_is_stored(
    app_factory: AppFactory,
) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_settings(pilot)
        screen = showing(app, SettingsScreen)
        row = app.services.settings.get_settings()
        assert row is not None

        assert screen.query_one("#input-leave-start", Input).value == (
            row.leave_year_start
        )
        assert screen.query_one("#input-working-days", Input).value == "Mon-Fri"
        assert screen.query_one("#input-auto-close", Input).value == row.auto_close_time
        assert screen.query_one("#select-division", Select).value == (
            row.bank_holiday_division
        )


async def test_screen_opens_before_any_settings_exist(tmp_path: Path) -> None:
    """`compose` falls back to defaults when there is no settings row to read."""
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
        assert screen.query_one("#input-working-days", Input).value == "Mon-Fri"
        assert screen.query_one("#input-auto-close", Input).value == "18:00"


# saving


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


async def test_empty_field_is_refused_and_writes_nothing(
    app_factory: AppFactory,
) -> None:
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


async def test_unreadable_time_is_refused(app_factory: AppFactory) -> None:
    """`save_settings` raises on a time it cannot parse, and the screen catches it."""
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


# entitlements


async def test_changed_entitlement_is_written(app_factory: AppFactory) -> None:
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


@pytest.mark.parametrize("allowance", ["loads", "-1", "nan", "inf"])
async def test_invalid_entitlement_is_refused(
    app_factory: AppFactory, allowance: str
) -> None:
    """A year outside the allowance domain stops the whole save.

    Settings and entitlements are parsed before anything is written, so a
    rejection leaves neither half on disk. The application hangs `invalidate()`
    off `dismiss(True)`, and a rejection does not dismiss.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        year = app.services.settings.active_leave_year()
        app.services.settings.save_entitlement(year, 25.0)
        was = stored_start(app)

        await open_settings(pilot)
        screen = showing(app, SettingsScreen)
        screen.query_one("#input-leave-start", Input).value = "07-07"
        screen.query_one(f"#ent-{year}", Input).value = allowance

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
    """The new row is a draft until Save, like every other field on the form."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        year = app.services.settings.active_leave_year()
        app.services.settings.save_entitlement(year, 22.0)

        await open_settings(pilot)
        await pilot.click("#btn-add-year")
        await pilot.pause()

        assert app.services.settings.get_entitlement(year + 1) is None, (
            "nothing is written until Save"
        )

        await pilot.click("#btn-save")
        await pilot.pause()

        added = app.services.settings.get_entitlement(year + 1)
        assert added is not None
        assert added.days == 22.0, "next year starts on the same allowance"


async def test_adding_a_year_with_none_on_record_defaults(
    app_factory: AppFactory,
) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        for row in app.services.settings.all_entitlements():
            app._session.delete(row)
        app._session.commit()

        await open_settings(pilot)
        await pilot.click("#btn-add-year")
        await pilot.pause()
        await pilot.click("#btn-save")
        await pilot.pause()

        year = app.services.settings.active_leave_year()
        added = app.services.settings.get_entitlement(year)
        assert added is not None
        assert added.days == 25.0


async def test_adding_a_year_keeps_the_form_intact(
    app_factory: AppFactory,
) -> None:
    """The button adds a row without dismissing.

    Dismissing would discard every field edited above it, and dismissing with
    `True` would tell the application the settings had changed.
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


async def test_f4_twice_does_not_build_a_second_form(
    app_factory: AppFactory,
) -> None:
    """`SettingsScreen.compose` reads every field as it is built.

    A second form stacked on the first is frozen at the values from before the
    first save, and saving it writes them back over the change.
    `action_go_to`'s early return cannot cover this: `self.nav` is only set for
    destinations with a nav item, and settings has none.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_settings(pilot)
        await open_settings(pilot)

        forms = [s for s in app.screen_stack if isinstance(s, SettingsScreen)]
        assert len(forms) == 1


async def test_leaving_with_settings_open_closes_both(
    app_factory: AppFactory,
) -> None:
    """`Screen.dismiss` pops the top of the stack, not the screen it is called on.

    With settings above the leave screen, dismissing the screen the application
    holds would take settings off and orphan the leave screen underneath.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("f2")
        await pilot.pause()
        await open_settings(pilot)
        showing(app, SettingsScreen)

        await pilot.press("f3")
        await pilot.pause()

        assert [type(screen).__name__ for screen in app.screen_stack] == [
            "Screen",
            "DashboardScreen",
            "InsightsScreen",
        ]


async def test_saving_redraws_the_screen_under_the_dialog(
    app_factory: AppFactory,
) -> None:
    """Settings is reachable from anywhere, so anything can be underneath it."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("f2")
        await pilot.pause()
        monday = date(2026, 6, 22)
        leave = showing(app, LeaveScreen)
        assert leave.query_one(YearCalendar).ledgers[monday].is_working_day

        await pilot.press("f4")
        await pilot.pause()
        showing(app, SettingsScreen).query_one(
            "#input-working-days", Input
        ).value = "Tue-Thu"
        await pilot.click("#btn-save")
        await pilot.pause()

        drawn = showing(app, LeaveScreen).query_one(YearCalendar).ledgers[monday]
        assert not drawn.is_working_day, (
            "the year is still measured against the working pattern that was replaced"
        )


# leaving


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


async def test_unowned_button_does_nothing(
    app_factory: AppFactory,
) -> None:
    """A screen that reacted to any button would react to one it does not own."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_settings(pilot)
        screen = showing(app, SettingsScreen)

        screen.on_button_pressed(Button.Pressed(Button("Nothing", id="btn-nothing")))
        await pilot.pause()

        showing(app, SettingsScreen), "neither saved nor dismissed"


# the working pattern, in words


@pytest.mark.parametrize(
    ("days", "shown"),
    [
        ((0, 1, 2, 3, 4), "Mon-Fri"),
        ((1, 3), "Tue, Thu"),
        ((0, 2, 4), "Mon, Wed, Fri"),
        ((2,), "Wed"),
    ],
)
def test_working_pattern_reads_as_days(days: tuple[int, ...], shown: str) -> None:
    """A run collapses, anything else is listed, and both re-parse to what they say."""
    assert describe_working_days(days) == shown
    assert parse_working_days(shown) == list(days)


async def test_pattern_field_names_days_not_numbers(
    app_factory: AppFactory,
) -> None:
    """`0,1,2,3,4` invites a reader to count from one.

    `1,2,3,4,5` is a valid answer and a week running Tuesday to Saturday, with
    nothing refused and nothing said.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_settings(pilot)
        field = showing(app, SettingsScreen).query_one("#input-working-days", Input)
        assert field.value == "Mon-Fri"
        assert field.placeholder == "Mon-Fri"

        await pilot.click("#btn-save")
        await pilot.pause()

        row = app.services.settings.get_settings()
        assert row is not None
        assert row.working_days == "0,1,2,3,4", "and it saves what it always saved"


# the form fits the screen


async def test_every_entitlement_year_can_be_reached(app_factory: AppFactory) -> None:
    """A third entitlement row has to stay reachable, not clip out of the dialog."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        for year, days in ((2026, 25.0), (2027, 26.0), (2028, 27.0)):
            app.services.settings.save_entitlement(year, days)

        await open_settings(pilot)
        assert "2026" in screen_text(app)

        showing(app, SettingsScreen).query_one("#ent-2028", Input).focus()
        await pilot.wait_for_scheduled_animations()
        await pilot.pause()

        assert "2028" in screen_text(app), "the field holding focus is drawn"


async def test_short_terminal_can_reach_the_whole_form(
    app_factory: AppFactory,
) -> None:
    """Twenty-four rows is a form taller than its terminal.

    The way out has to be on screen and the entitlements under it have to
    scroll into view.
    """
    app = app_factory()
    async with app.run_test(size=(80, 24)) as pilot:
        app.services.settings.save_entitlement(2026, 25.0)

        await open_settings(pilot)
        await pilot.pause()

        shown = screen_text(app)
        assert "Save" in shown
        assert "Back" in shown

        showing(app, SettingsScreen).query_one("#ent-2026", Input).focus()
        await pilot.wait_for_scheduled_animations()
        await pilot.pause()

        assert "2026" in screen_text(app), "the field holding focus is drawn"


async def test_added_year_is_shown_not_only_announced(
    app_factory: AppFactory,
) -> None:
    app = app_factory()
    async with app.run_test(size=(80, 24)) as pilot:
        for year, days in ((2026, 25.0), (2027, 26.0), (2028, 27.0)):
            app.services.settings.save_entitlement(year, days)

        await open_settings(pilot)
        await pilot.click("#btn-add-year")
        await pilot.pause()
        await pilot.wait_for_scheduled_animations()
        await pilot.pause()

        assert "2029" in screen_text(app)


# enter


async def test_enter_in_a_field_saves(app_factory: AppFactory) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_settings(pilot)
        screen = showing(app, SettingsScreen)
        field = screen.query_one("#input-auto-close", Input)
        field.focus()
        field.value = "19:00"
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()

        showing(app, DashboardScreen)
        row = app.services.settings.get_settings()
        assert row is not None
        assert row.auto_close_time == "19:00"
