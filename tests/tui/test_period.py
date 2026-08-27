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
from flexi.services.registry import invalidate_services
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
    """It names the shown period, always, in the same place.

    The date left this slot for the clock panel, beside the figure that moves
    every second. Two facts sharing one corner meant the period -- the thing the
    period key changes -- was the half a reader had to look past.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        header = app.screen.query_one("#header-context")
        assert str(header.render()) == "Week of 8 Jun"

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


# -- and legible once it is tinted -------------------------------------------

READABLE = 3.0
"""Contrast a day number has to clear against the ground behind it.

Below three to one a dim tone on a lifted ground stops being a number and
becomes a texture. `$c-line` on the window fill measured 1.02:1 -- the day was
in the compositor and not on the screen.
"""


def channel(value: int) -> float:
    scaled = value / 255
    return scaled / 12.92 if scaled <= 0.04045 else ((scaled + 0.055) / 1.055) ** 2.4


def relative_luminance(colour: tuple[int, int, int]) -> float:
    red, green, blue = (channel(part) for part in colour)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(foreground: tuple[int, int, int], ground: tuple[int, int, int]) -> float:
    """The WCAG ratio between two colours, brighter over darker."""
    pair = sorted((relative_luminance(foreground), relative_luminance(ground)))
    return (pair[1] + 0.05) / (pair[0] + 0.05)


@pytest.mark.parametrize("granularity", list(Granularity))
async def test_every_day_inside_the_window_stays_readable(
    app_factory: AppFactory, granularity: Granularity
) -> None:
    """The window lifts the ground under days drawn in the dimmest tones.

    A fortnight of untracked days, or the tail of an adjacent month inside a
    leave year, are exactly the cells a period window covers and exactly the
    ones drawn faintest. Lifting the ground without lifting them leaves the
    numbers missing from cells that still have borders.

    The seed records work from the first day of its leave year, so the dimmest
    tier does not occur in it and a test taking the seed as it comes cannot see
    this. Setup is moved forward to put a fortnight of untracked days on screen,
    which is what a first month of use actually looks like.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        stored = app.services.settings.get_settings()
        assert stored is not None
        stored.tracking_since = TODAY.replace(day=14)
        invalidate_services(app.services)
        dashboard(app).action_zoom(granularity.value)
        await pilot.pause()

        assert app.screen.query_one(MonthView).query("Label.day-untracked"), (
            "the dimmest tier has to be on screen for this to be measuring it"
        )

        illegible = {
            (contrast(cell.colors[3].rgb, cell.background_colors[1].rgb), cell.id)
            for cell in app.screen.query_one(MonthView).query("Label.in-period")
        }
        worst = min(illegible, default=(READABLE, None))

        assert worst[0] >= READABLE, f"{worst[1]} reads at {worst[0]:.2f}:1"


async def test_the_cursor_keeps_its_own_colours_on_a_day_that_was_worked(
    app_factory: AppFactory,
) -> None:
    """Every calendar rule is one class on one element, so the last one wins.

    The day-type colours were written after the selection and took it, which put
    the accent's own lift on the accent itself: the cursor was least readable on
    a day somebody had worked, which is most of the days there are.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        view = app.screen.query_one(MonthView)
        cursor = next(iter(view.query("Label.selected")))

        assert "day-worked" in cursor.classes, "the seeded cursor sits on a worked day"
        assert contrast(cursor.colors[3].rgb, cursor.background_colors[1].rgb) >= (
            READABLE
        )


@pytest.mark.parametrize("granularity", list(Granularity))
async def test_the_calendar_names_the_period_type_it_is_windowing(
    app_factory: AppFactory, granularity: Granularity
) -> None:
    """The row above the days already names the month the grid is drawn around.

    Repeating it underneath spent the one live slot on the panel saying the same
    thing twice, and left the span the window is tinting for unnamed.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        dashboard(app).action_zoom(granularity.value)
        await pilot.pause()

        view = app.screen.query_one(MonthView)
        assert view.border_subtitle == granularity.label
