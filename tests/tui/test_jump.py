"""Feature 5: jump mode."""

from __future__ import annotations

import pytest

from flexi.components.expandable import DAY, ExpandableTable
from flexi.components.jump_overlay import JumpOverlay
from flexi.components.modules.calendar import CalendarModule
from flexi.components.modules.clock import ClockModule
from tests.tui.conftest import WIDE, dashboard

pytestmark = pytest.mark.usefixtures("_frozen")


async def test_v_opens_the_overlay(app_factory) -> None:
    """It puts a badge over every jumpable region."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("v")
        await pilot.pause()
        assert isinstance(app.screen, JumpOverlay)
        labels = {
            str(widget.render()) for widget in app.screen.query(".textual-jump-label")
        }
        assert {"c", "b", "w", "r", "p"} <= labels


async def test_a_target_key_focuses_that_panel(app_factory) -> None:
    """It lands where the badge said it would."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("v")
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.focused, ClockModule)


async def test_a_jump_to_the_records_lands_on_the_rows(app_factory) -> None:
    """It focuses the table, not the panel around it."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("v")
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        assert isinstance(app.focused, ExpandableTable)


async def test_a_number_jumps_to_a_day_row(app_factory) -> None:
    """It puts the cursor on the nth day without leaving the home row.

    Flexi's extension to the reference application's jump mode, and the reason jump mode earns its
    place in a table-heavy application.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("v")
        await pilot.pause()
        await pilot.press("4")
        await pilot.pause()

        table = app.screen.query_one("#records-table", ExpandableTable)
        assert table.cursor_key == f"{DAY}2026-06-11"
        assert app.focused is table


async def test_escape_restores_the_previous_focus_exactly(app_factory) -> None:
    """It costs nothing to try, which is what makes the mode worth having."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        calendar = app.screen.query_one(CalendarModule)
        calendar.focus()
        await pilot.pause()

        await pilot.press("v")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert app.focused is calendar


async def test_targets_come_from_the_live_screen(app_factory) -> None:
    """It can only name something that is mounted.

    the reference application keeps one application-wide dict and silently drops the misses; asking
    the screen means a target that is not there simply is not offered.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        targets = dashboard(app).jump_targets()
        for widget_id in targets:
            if widget_id.startswith(DAY):
                continue
            assert app.screen.query(f"#{widget_id}"), f"{widget_id} is not mounted"
