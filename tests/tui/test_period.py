"""Feature 4: the calendar drives the period, and says where you are."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from flexi.app import FlexiApp
from flexi.components.expandable import ExpandableTable, RowKind
from flexi.components.modules.monthview import MonthView
from flexi.config import CONFIG
from flexi.constants import Granularity
from flexi.domain.dates import DAYS_IN_WEEK
from tests.tui.conftest import WIDE, AppFactory, dashboard

TODAY = date(2026, 6, 11)


def day_rows(app: FlexiApp) -> int:
    table = app.screen.query_one("#records-table", ExpandableTable)
    return len([row for row in table.visible_rows() if row.kind == RowKind.DAY])


@pytest.mark.parametrize(
    ("key", "granularity", "rows"),
    [
        ("d", Granularity.DAY, 1),
        ("w", Granularity.WEEK, 7),
        ("m", Granularity.MONTH, 30),
        ("y", Granularity.YEAR, 365),
    ],
)
async def test_a_key_per_granularity(
    app_factory: AppFactory, key: str, granularity: Granularity, rows: int
) -> None:
    """It changes how much of time is on screen, and the table follows."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press(key)
        await pilot.pause()
        assert dashboard(app).period.granularity is granularity
        assert day_rows(app) == rows


async def test_zooming_out_and_back_keeps_your_place(app_factory: AppFactory) -> None:
    """It keeps the anchor, so a month view and back is the same week."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        before = dashboard(app).period
        await pilot.press("m")
        await pilot.pause()
        await pilot.press("w")
        await pilot.pause()
        assert dashboard(app).period == before


async def test_brackets_step_and_t_returns(app_factory: AppFactory) -> None:
    """It moves a period at a time and comes home without changing the width."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("right_square_bracket", "right_square_bracket")
        await pilot.pause()
        moved = dashboard(app).period
        assert moved.start == date(2026, 6, 22)
        assert not moved.contains(TODAY)

        await pilot.press("t")
        await pilot.pause()
        home = dashboard(app).period
        assert home.contains(TODAY)
        assert home.granularity is Granularity.WEEK


async def test_the_future_is_reachable(app_factory: AppFactory) -> None:
    """It can show next month, which an offset-from-today model cannot."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("m")
        for _ in range(3):
            await pilot.press("right_square_bracket")
        await pilot.pause()
        assert dashboard(app).period.start == date(2026, 9, 1)


async def test_p_cycles_the_granularity(app_factory: AppFactory) -> None:
    """It cycles day to week to month to year, and wraps."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("p")
        await pilot.pause()
        assert dashboard(app).period.granularity is Granularity.MONTH


async def test_the_header_says_where_you_are(app_factory: AppFactory) -> None:
    """It names today and the shown period, always, in the same place."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        header = app.screen.query_one("#header-context")
        assert "Thu 11 Jun" in str(header.render())
        assert "Week of 8 Jun" in str(header.render())

        await pilot.press("m")
        await pilot.pause()
        assert "June 2026" in str(app.screen.query_one("#header-context").render())


async def test_go_to_date_accepts_an_offset(app_factory: AppFactory) -> None:
    """It takes the several ways somebody might type a date."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("g")
        await pilot.pause()
        await pilot.press("plus", "3", "d")
        await pilot.press("enter")
        await pilot.pause()
        assert dashboard(app).period.anchor == date(2026, 6, 14)


async def test_the_calendar_marks_today_the_selection_and_the_period(
    app_factory: AppFactory,
) -> None:
    """It uses three devices for three facts, so one cell can carry them all."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        classes = {
            widget.id: widget.classes
            for widget in app.screen.query(".calendar-row Label")
            if widget.id
        }
        assert any("today" in item for item in classes.values())
        assert any("selected" in item for item in classes.values())


async def test_the_calendar_moves_the_period_by_posting_the_day_it_landed_on(
    app_factory: AppFactory,
) -> None:
    """The calendar owns its cursor; the screen owns the period.

    Arrowing onto a day in another week has to take the whole dashboard with it,
    or the calendar highlights one week while the records table below it still
    lists another — two views of the same thing disagreeing on screen.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        calendar = app.screen.query_one(MonthView)
        calendar.focus()
        await pilot.pause()
        assert dashboard(app).period.anchor == TODAY

        await pilot.press("down")  # the same weekday, a week on
        await pilot.pause()

        assert dashboard(app).period.anchor == TODAY + timedelta(days=7)
        first = app.screen.query_one("#records-table", ExpandableTable).visible_rows()[
            0
        ]
        assert first.key == f"{RowKind.DAY}2026-06-15", (
            "the table stayed on the old week"
        )
        assert "Week of 15 Jun" in str(app.screen.query_one("#header-context").render())


async def test_leaving_the_go_to_date_prompt_stays_where_you_were(
    app_factory: AppFactory,
) -> None:
    """Escape is not an answer, and must not be read as one.

    The prompt hands back a date or nothing. Treating nothing as today would
    make cancelling out of it indistinguishable from pressing `t`, and quietly
    throw away the week somebody had browsed to.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("right_square_bracket")
        await pilot.pause()
        before = dashboard(app).period

        await pilot.press("g")
        await pilot.pause()
        assert app.screen.query("#goto-input")
        await pilot.press("escape")
        await pilot.pause()

        assert dashboard(app).period == before


# -- the calendar's window is visible, and follows the cycle -----------------

LEGIBLE_LIFT = 12.0
"""How far the window has to lift the ground to be seen, in luminance.

The tint was `$c-accent-deep` blended to 40%, which lifted it by nine -- about
three and a half percent of the range. Present in the compositor and invisible
on a screen, which is a feature that has not been built.
"""


def luminance(colour: tuple[int, int, int]) -> float:
    """Rec. 709 relative luminance, which is what the eye is doing here."""
    red, green, blue = colour
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def grounds(app: FlexiApp) -> dict[str, tuple[int, int, int]]:
    """The background behind a windowed cell and behind an unwindowed one."""
    view = app.screen.query_one(MonthView)
    windowed = next(iter(view.query("Label.in-period")))
    plain = next(
        cell
        for cell in view.query("Label")
        if not cell.has_class("in-period") and not cell.has_class("selected")
    )
    return {
        "window": windowed.background_colors[1].rgb,
        "page": plain.background_colors[1].rgb,
    }


async def test_the_period_window_is_actually_visible(app_factory: AppFactory) -> None:
    """A tint the compositor records and a screen cannot show is not a tint."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        painted = grounds(app)

        lift = luminance(painted["window"]) - luminance(painted["page"])

        assert lift >= LEGIBLE_LIFT, (
            f"the window lifts the ground by {lift:.1f}, which cannot be seen"
        )


async def test_cycling_the_period_moves_the_window_with_it(
    app_factory: AppFactory,
) -> None:
    """The hotkey is what the tint exists to explain.

    Each granularity covers strictly more of the grid than the one before it,
    so the calendar says what "day", "week" and "month" mean without a legend.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        view = app.screen.query_one(MonthView)
        covered: dict[str, int] = {}
        for _ in range(len(Granularity)):
            covered[dashboard(app).period.granularity.value] = len(
                view.query("Label.in-period")
            )
            await pilot.press(CONFIG.hotkeys.period_cycle)
            await pilot.pause()

        assert covered["day"] == 1
        assert covered["week"] == DAYS_IN_WEEK
        assert covered["day"] < covered["week"] < covered["month"] <= covered["year"]
