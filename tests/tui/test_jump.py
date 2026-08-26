"""Feature 5: jump mode."""

from __future__ import annotations

import pytest
from textual import events
from textual.message import Message
from textual.widgets import Static

from flexi.components.expandable import ExpandableTable, RowKind
from flexi.components.jump_overlay import JumpOverlay
from flexi.components.modules.clock import ClockModule
from flexi.components.modules.monthview import MonthView
from flexi.components.modules.records import RecordsModule
from flexi.screens.leave import LeaveScreen
from tests.tui.conftest import WIDE, AppFactory, dashboard, showing


class Beacon(Static):
    """A widget that names its own jump key, as ``Jumpable`` allows.

    Every target on the shipped screens is registered by id in the screen's
    ``jump_targets``. The protocol is the other route — a widget that carries its
    own key — and nothing in the application uses it yet, so this stands in for
    the first thing that does.
    """

    jump_key = "z"
    can_focus = True


def badges(overlay: JumpOverlay) -> set[str]:
    """The keys the overlay is currently offering."""
    return {str(widget.render()) for widget in overlay.query(".textual-jump-label")}


async def test_v_opens_the_overlay(app_factory: AppFactory) -> None:
    """It puts a badge over every jumpable region."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("v")
        await pilot.pause()
        assert {"c", "b", "w", "r", "p"} <= badges(showing(app, JumpOverlay))


async def test_a_target_key_focuses_that_panel(app_factory: AppFactory) -> None:
    """It lands where the badge said it would."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("v")
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.focused, ClockModule)


async def test_a_jump_to_the_records_lands_on_the_rows(app_factory: AppFactory) -> None:
    """It focuses the table, not the panel around it."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("v")
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        assert isinstance(app.focused, ExpandableTable)


async def test_a_number_jumps_to_a_day_row(app_factory: AppFactory) -> None:
    """It puts the cursor on the nth day without leaving the home row.

    A row is not a widget, which is what makes this worth a test: the offsets
    come from the table's geometry rather than from the DOM.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("v")
        await pilot.pause()
        await pilot.press("4")
        await pilot.pause()

        table = app.screen.query_one("#records-table", ExpandableTable)
        assert table.cursor_key == f"{RowKind.DAY}2026-06-11"
        assert app.focused is table


async def test_escape_restores_the_previous_focus_exactly(
    app_factory: AppFactory,
) -> None:
    """It costs nothing to try, which is what makes the mode worth having."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        calendar = app.screen.query_one(MonthView)
        calendar.focus()
        await pilot.pause()

        await pilot.press("v")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert app.focused is calendar


async def test_targets_come_from_the_live_screen(app_factory: AppFactory) -> None:
    """It can only name something that is mounted.

    An application-wide table of targets would silently drop the misses; asking
    the screen means a target that is not there simply is not offered.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        targets = dashboard(app).jump_targets()
        for widget_id in targets:
            if widget_id.startswith(RowKind.DAY):
                continue
            assert app.screen.query(f"#{widget_id}"), f"{widget_id} is not mounted"


async def test_a_widget_carrying_its_own_key_is_offered_and_focused(
    app_factory: AppFactory,
) -> None:
    """A target does not have to be in the screen's table to be jumpable.

    The badge for a registered panel dismisses with an id and the app looks it
    up; this one dismisses with the widget itself. Two shapes of the same
    answer, and the second is the one no shipped screen exercises — so it is the
    one that would rot into an `AttributeError` on the first widget to use it.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await app.screen.query_one("#dashboard-controls").mount(
            Beacon("beacon"), before=0
        )
        await pilot.pause()

        await pilot.press("v")
        await pilot.pause()
        assert "z" in badges(showing(app, JumpOverlay))

        await pilot.press("z")
        await pilot.pause()
        assert isinstance(app.focused, Beacon)


async def test_opening_the_mode_with_nothing_focused_and_escaping_jumps_nowhere(
    app_factory: AppFactory,
) -> None:
    """Both ends of the mode assume a focus, and neither may require one.

    Nothing is focused after a click on dead space, and the mode has to cope: no
    focus to stand down on the way in, and none to hand back on the way out.
    Escape is a promise that nothing happened, and jumping somewhere arbitrary
    because there was nowhere to return to breaks it.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        app.set_focus(None)
        await pilot.pause()
        assert app.focused is None

        await pilot.press("v")
        await pilot.pause()
        showing(app, JumpOverlay)
        await pilot.press("escape")
        await pilot.pause()

        panels = [
            widget
            for target in dashboard(app).jump_targets()
            for widget in app.screen.query(f"#{target}")
        ]
        assert panels, "the dashboard should still be offering its panels"
        assert app.focused not in panels, "escape landed on a badge nobody pressed"


async def test_a_badge_for_a_panel_that_has_gone_says_so_and_moves_nothing(
    app_factory: AppFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The badges are drawn once and the screen underneath keeps living.

    A redraw that drops a panel between the badge appearing and the key being
    pressed leaves a key pointing at nothing. Focusing whatever happens to be
    lying around instead would be worse than doing nothing, so the miss is
    logged and the keyboard stays where it was.
    """
    warnings: list[str] = []

    class Recorder:
        def warning(self, message: object) -> None:
            warnings.append(str(message))

    monkeypatch.setattr("flexi.app.log", Recorder())

    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        before = app.focused

        await pilot.press("v")
        await pilot.pause()
        await app.screen_stack[-2].query_one(MonthView).remove()
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause()

        assert app.focused is before, "a missing target moved the focus anyway"
        assert any("month-view" in line for line in warnings), warnings


async def test_a_row_badge_for_a_table_that_has_gone_moves_nothing(
    app_factory: AppFactory,
) -> None:
    """A row key is resolved against the table, not against the DOM.

    A day row has no id to look up, so the miss cannot be reported the way a
    missing panel is: the app searches the screen for a table to put the cursor
    in and finds none. Falling through to the id lookup below would then warn
    about a widget nobody ever named.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        before = app.focused

        await pilot.press("v")
        await pilot.pause()
        assert "4" in badges(showing(app, JumpOverlay))
        await app.screen_stack[-2].query_one(RecordsModule).remove()
        await pilot.pause()
        await pilot.press("4")
        await pilot.pause()

        assert app.focused is before


async def test_the_row_badges_are_not_offered_without_a_records_table(
    app_factory: AppFactory,
) -> None:
    """The numbers are the table's rows, so no table means no numbers.

    They are collected by asking the records module for its geometry. A screen
    that has lost the module has to answer "none" rather than raise, because the
    overlay is composed from that answer and a failed compose is a black screen
    over a working application.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await app.screen.query_one(RecordsModule).remove()
        await pilot.pause()

        await pilot.press("v")
        await pilot.pause()
        offered = badges(showing(app, JumpOverlay))

        assert not offered & set("123456789"), f"rows were offered: {offered}"
        assert "c" in offered, "the surviving panels are still jumpable"


async def test_a_panel_that_cannot_take_focus_is_clicked_instead(
    app_factory: AppFactory,
) -> None:
    """A jump has to be able to press things, not only focus them.

    The leave screen's rail is plain containers: they hold the wallet, the
    selection and the legend, and none of them can hold the keyboard. Focusing
    them anyway would take the arrow keys off the calendar and leave the screen
    unusable, so the app synthesises the click a pointer would have made.
    """
    clicked: list[object] = []

    def watch(message: Message) -> None:
        if isinstance(message, events.Click):
            clicked.append(message.widget)

    app = app_factory()
    async with app.run_test(size=WIDE, message_hook=watch) as pilot:
        await pilot.press("f2")
        await pilot.pause()
        screen = showing(app, LeaveScreen)
        calendar = screen.calendar
        assert app.focused is calendar

        await pilot.press("v")
        await pilot.pause()
        await pilot.press("b")  # the legend, a container that cannot focus
        await pilot.pause()

        assert app.focused is calendar, "the keyboard left the calendar"
        assert screen.query_one("#leave-legend") in clicked
