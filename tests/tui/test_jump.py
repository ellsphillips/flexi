"""Feature 5: jump mode."""

from __future__ import annotations

import pytest
from textual import events
from textual.css.query import DOMQuery
from textual.geometry import Offset
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Static

from flexi.components.expandable import ExpandableTable, RowKind
from flexi.components.jump_overlay import JumpOverlay, badge_offset
from flexi.components.jumper import BadgeShape
from flexi.components.modules.clock import ClockModule
from flexi.components.modules.monthview import MonthView
from flexi.components.modules.records import RecordsModule
from flexi.screens.leave import LeaveScreen
from tests.tui.conftest import WIDE, AppFactory, dashboard, showing


class Beacon(Static):
    """A widget that names its own jump key, as ``Jumpable`` allows.

    Every target on the shipped screens is registered by id in the screen's
    ``jump_targets``; no shipped widget carries its own key.
    """

    jump_key = "z"
    can_focus = True


def badges(overlay: JumpOverlay) -> set[str]:
    """Return the keys the overlay is currently offering."""
    return {str(widget.render()) for widget in overlay.query(".textual-jump-label")}


async def test_v_opens_the_overlay(app_factory: AppFactory) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("v")
        await pilot.pause()
        assert {"c", "b", "w", "r", "p"} <= badges(showing(app, JumpOverlay))


async def test_a_target_key_focuses_that_panel(app_factory: AppFactory) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("v")
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.focused, ClockModule)


async def test_a_jump_to_the_records_lands_on_the_rows(app_factory: AppFactory) -> None:
    """The table takes the focus, not the panel around it."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("v")
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        assert isinstance(app.focused, ExpandableTable)


async def test_a_number_jumps_to_a_day_row(app_factory: AppFactory) -> None:
    """A row is not a widget: the offsets come from the table's geometry."""
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


async def test_escape_restores_the_previous_focus(
    app_factory: AppFactory,
) -> None:
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
    """A target that is not mounted is not offered."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        targets = dashboard(app).jump_targets()
        for widget_id in targets:
            if widget_id.startswith(RowKind.DAY):
                continue
            assert app.screen.query(f"#{widget_id}"), f"{widget_id} is not mounted"


async def test_a_widget_with_its_own_key_is_jumpable(
    app_factory: AppFactory,
) -> None:
    """A registered badge dismisses with an id, this one with the widget."""
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


async def test_escape_with_nothing_focused_jumps_nowhere(
    app_factory: AppFactory,
) -> None:
    """Both ends of the mode assume a focus, and neither may require one."""
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


async def test_a_badge_for_a_missing_panel_moves_nothing(
    app_factory: AppFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A redraw between the badge and the keypress leaves a key pointing nowhere."""
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


async def test_a_row_badge_for_a_missing_table_moves_nothing(
    app_factory: AppFactory,
) -> None:
    """A row key is resolved against the table, not against the DOM."""
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


async def test_row_badges_need_a_records_table(
    app_factory: AppFactory,
) -> None:
    """A failed compose is a black screen, so a missing module answers none."""
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


async def test_a_panel_that_cannot_focus_is_clicked(
    app_factory: AppFactory,
) -> None:
    """Focusing a container would take the arrow keys off the calendar."""
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


# How a badge is drawn


def heights(badges: DOMQuery[Widget]) -> set[int | None]:
    """Return the resolved height of every badge in a query."""
    return {
        None if badge.styles.height is None else int(badge.styles.height.value)
        for badge in badges
    }


@pytest.mark.parametrize(
    ("corner", "expected"),
    [
        (Offset(20, 8), Offset(19, 7)),
        (Offset(0, 8), Offset(0, 7)),
        (Offset(20, 0), Offset(19, 0)),
        (Offset(0, 0), Offset(0, 0)),
    ],
)
def test_a_corner_badge_never_hangs_off_the_screen(
    corner: Offset, expected: Offset
) -> None:
    """A negative offset clips the side that left the screen, losing a border."""
    assert badge_offset(corner, BadgeShape.CORNER) == expected


def test_a_row_badge_is_not_nudged() -> None:
    """A row chip names one line, and a line above is a different day."""
    assert badge_offset(Offset(20, 8), BadgeShape.ROW) == Offset(20, 8)


async def test_panels_are_boxed_and_table_rows_are_not(
    app_factory: AppFactory,
) -> None:
    """Rows sit a single cell apart, so only a panel can afford a three-line box."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("v")
        await pilot.pause()
        await pilot.pause()
        overlay = showing(app, JumpOverlay)

        boxed = overlay.query(".textual-jump-label.-corner")
        chips = overlay.query(".textual-jump-label.-row")

        assert boxed, "the dashboard's panels are jumpable"
        assert chips, "and so are the records table's day rows"
        assert heights(boxed) == {3}, "a box: border, key, border"
        assert heights(chips) == {1}, "a chip: the key and nothing over it"
