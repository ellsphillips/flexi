"""The calendar drives the period, and the header says which one is showing."""

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
from tests.tui.conftest import READABLE, WIDE, AppFactory, contrast, dashboard

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
async def test_each_granularity_has_a_key(
    app_factory: AppFactory, key: str, granularity: Granularity, rows: int
) -> None:
    """The key changes how much time is on screen, and the table follows."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press(key)
        await pilot.pause()
        assert dashboard(app).period.granularity is granularity
        assert day_rows(app) == rows


async def test_zooming_out_and_back_keeps_your_place(app_factory: AppFactory) -> None:
    """The anchor is kept, so a month view and back is the same week."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        before = dashboard(app).period
        await pilot.press("m")
        await pilot.pause()
        await pilot.press("w")
        await pilot.pause()
        assert dashboard(app).period == before


async def test_brackets_step_and_t_returns(app_factory: AppFactory) -> None:
    """A bracket moves one period; `t` comes home without changing the width."""
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


async def test_future_periods_are_reachable(app_factory: AppFactory) -> None:
    """The period is anchored to a date, so a future month is reachable."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("m")
        for _ in range(3):
            await pilot.press("right_square_bracket")
        await pilot.pause()
        assert dashboard(app).period.start == date(2026, 9, 1)


async def test_p_cycles_the_granularity(app_factory: AppFactory) -> None:
    """The cycle runs day, week, month, year, and wraps."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("p")
        await pilot.pause()
        assert dashboard(app).period.granularity is Granularity.MONTH


async def test_header_says_which_period_is_showing(app_factory: AppFactory) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        header = app.screen.query_one("#header-context")
        assert str(header.render()) == "Week of 8 Jun"

        await pilot.press("m")
        await pilot.pause()
        assert "June 2026" in str(app.screen.query_one("#header-context").render())


async def test_go_to_date_accepts_an_offset(app_factory: AppFactory) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("g")
        await pilot.pause()
        await pilot.press("plus", "3", "d")
        await pilot.press("enter")
        await pilot.pause()
        assert dashboard(app).period.anchor == date(2026, 6, 14)


async def test_calendar_marks_today_and_the_selection(
    app_factory: AppFactory,
) -> None:
    """Three devices for three facts, so one cell can carry them all."""
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


async def test_calendar_posts_the_day_it_landed_on(
    app_factory: AppFactory,
) -> None:
    """The calendar asks for a day; the screen owns the period.

    Arrowing onto a day in another week takes the whole dashboard with it, or
    the calendar and the records table below it show different weeks.
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


async def test_escaping_go_to_date_changes_nothing(
    app_factory: AppFactory,
) -> None:
    """The prompt hands back a date or nothing, and nothing is not today."""
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


# The period window ----------------------------------------------------------

LEGIBLE_LIFT = 12.0
"""How far the window has to lift the ground to be seen, in luminance."""


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


async def test_period_window_is_visible(app_factory: AppFactory) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        painted = grounds(app)

        lift = luminance(painted["window"]) - luminance(painted["page"])

        assert lift >= LEGIBLE_LIFT, (
            f"the window lifts the ground by {lift:.1f}, which cannot be seen"
        )


async def test_cycling_the_period_moves_the_window(
    app_factory: AppFactory,
) -> None:
    """Each granularity covers more of the grid than the one before it.

    The calendar says what "day", "week" and "month" mean without a legend.
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


# Legibility under the tint --------------------------------------------------


@pytest.mark.parametrize("granularity", list(Granularity))
async def test_every_day_inside_the_window_stays_readable(
    app_factory: AppFactory, granularity: Granularity
) -> None:
    """The window lifts the ground under days drawn in the dimmest tones.

    The seed records work from the first day of its leave year, so the untracked
    tier does not occur in it; tracking is moved forward here to put a fortnight
    of untracked days on screen.
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


async def test_cursor_keeps_its_colours_on_worked_days(
    app_factory: AppFactory,
) -> None:
    """Every calendar rule is one class on one element, so the last one wins."""
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
async def test_calendar_names_the_period_it_is_windowing(
    app_factory: AppFactory, granularity: Granularity
) -> None:
    """The row above the days names the month, so the subtitle names the span."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        dashboard(app).action_zoom(granularity.value)
        await pilot.pause()

        view = app.screen.query_one(MonthView)
        assert view.border_subtitle == granularity.label
