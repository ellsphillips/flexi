"""Phase 3: the charts, and the rules they keep."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from textual.containers import VerticalScroll
from textual.pilot import Pilot
from textual.widgets import Input

from flexi.app import FlexiApp
from flexi.components.charts import (
    Burndown,
    DivergingBars,
    WeekRibbon,
    YearHeatmap,
    week_columns,
)
from flexi.components.chrome import AppHeader
from flexi.components.modules.base import Module
from flexi.components.plot import Plot
from flexi.config import CONFIG
from flexi.constants import DayKind, Granularity
from flexi.domain.ledger import DayLedger
from flexi.messages import Scope
from flexi.screens.insights import BalanceHistory, InsightsScreen
from flexi.screens.settings import SettingsScreen
from flexi.services.settings import SettingsUpdate
from tests.tui.conftest import WIDE, AppFactory, screen_text, showing

CONTRACTED = timedelta(minutes=444)


def ledger(
    when: date, worked: timedelta, expected: timedelta = CONTRACTED
) -> DayLedger:
    return DayLedger(
        date=when,
        kind=DayKind.WORKING,
        is_working_day=True,
        contracted=CONTRACTED,
        worked=worked,
        expected=expected,
    )


# ---- pure helpers ----


def test_week_columns_group_days_into_weeks() -> None:
    """Buckets by the Monday, so a bar is a week whatever day it starts on."""
    days = [
        ledger(date(2026, 6, 8), CONTRACTED + timedelta(hours=1)),
        ledger(date(2026, 6, 9), CONTRACTED),
        ledger(date(2026, 6, 15), CONTRACTED - timedelta(hours=2)),
    ]
    columns = week_columns(days, first_weekday=0)
    assert [column.label for column in columns] == ["8", "15"]
    assert columns[0].value == pytest.approx(1.0)
    assert columns[1].readout == "−2:00"


def test_week_columns_of_nothing_is_empty() -> None:
    assert week_columns([], first_weekday=0) == []


# ---- the screen ----


async def test_f3_opens_insights_on_the_leave_year(app_factory: AppFactory) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        assert showing(app, InsightsScreen).period.start == date(2026, 4, 6)


async def test_escape_returns_to_the_dashboard(app_factory: AppFactory) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, InsightsScreen)
        assert app.nav == "dashboard"


async def test_f1_returns_to_the_dashboard(app_factory: AppFactory) -> None:
    """Insights is a pushed screen, so `f1` dismisses it as well as setting nav."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        showing(app, InsightsScreen)

        await pilot.press("f1")
        await pilot.pause()
        assert not isinstance(app.screen, InsightsScreen)
        assert app.nav == "dashboard"


async def test_every_chart_draws(app_factory: AppFactory) -> None:
    """Every panel renders with data, not an empty state."""
    app = app_factory()
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        for chart in (Plot, DivergingBars, Burndown, WeekRibbon, YearHeatmap):
            assert app.screen.query_one(chart)
        text = screen_text(app)
        assert "Nothing recorded yet" not in text
        assert "No entitlement recorded" not in text


async def test_balance_chart_stops_at_today(app_factory: AppFactory) -> None:
    """Days not yet lived would chart as a cliff of deficits."""
    app = app_factory()
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        subtitle = str(app.screen.query_one("#balance-history").border_subtitle)
        assert "11 Jun" in subtitle
        assert subtitle.startswith("+"), f"expected a surplus, got {subtitle!r}"


async def test_every_chart_writes_its_figures(
    app_factory: AppFactory,
) -> None:
    """No chart is the only way to read its own numbers.

    The page scrolls and holds more charts than fit a terminal, so the whole
    page is read, not one viewport.
    """
    app = app_factory()
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        text = await scrolled_text(app, pilot)
        assert "best" in text
        assert "worst" in text
        assert "taken" in text
        assert "left" in text
        assert "+" in text
        assert "−" in text


async def scrolled_text(app: FlexiApp, pilot: Pilot[None]) -> str:
    """Gather everything the page says, a screenful at a time."""
    body = app.screen.query_one(VerticalScroll)
    seen = [screen_text(app)]
    while body.scroll_offset.y < body.max_scroll_y:
        body.scroll_page_down(animate=False)
        await pilot.pause()
        seen.append(screen_text(app))
    return "\n".join(seen)


async def test_heatmap_legend_names_both_ends(app_factory: AppFactory) -> None:
    """A diverging ramp needs both poles labelled to be a scale."""
    app = app_factory()
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        legend = str(app.screen.query_one(YearHeatmap).render()).splitlines()[-1]
        assert legend.startswith("−")
        assert "+" in legend


async def test_insights_panels_are_jumpable(app_factory: AppFactory) -> None:
    app = app_factory()
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        insights = showing(app, InsightsScreen)
        for widget_id in insights.jump_targets():
            assert insights.query(f"#{widget_id}"), f"{widget_id} is not mounted"


# ---- moving the period ----


async def test_year_that_has_not_begun_says_so(
    app_factory: AppFactory,
) -> None:
    """The balance chart stops at today.

    A period beginning after today has nothing between the two, and an empty
    chart reads as a bug, so the panel says which it is.
    """
    app = app_factory()
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.press("f3")
        await pilot.pause()

        await pilot.press("right_square_bracket")
        await pilot.pause()

        insights = showing(app, InsightsScreen)
        assert insights.period.start == date(2027, 4, 6)
        assert str(insights.query_one("#balance-history").border_subtitle) == (
            "not started"
        )
        bars = insights.query_one("#balance-bars", DivergingBars)
        assert "Nothing recorded yet" in str(bars.render())


async def test_today_brings_the_charts_back(
    app_factory: AppFactory,
) -> None:
    app = app_factory()
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        await pilot.press("right_square_bracket")
        await pilot.pause()

        await pilot.press("t")
        await pilot.pause()

        insights = showing(app, InsightsScreen)
        assert insights.period.start == date(2026, 4, 6)
        assert "11 Jun" in str(insights.query_one("#balance-history").border_subtitle)


async def test_cycling_the_period_relabels_the_header(
    app_factory: AppFactory,
) -> None:
    """Zooming redraws every panel, and the header names the span they cover."""
    app = app_factory()
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        # Next year, not this one: `p` narrows a leave year to a single day,
        # and `DivergingBars._arms` divides by `high + low`, zero whenever
        # every bar holds the same value. A year charting nothing survives it.
        await pilot.press("right_square_bracket")
        await pilot.pause()

        await pilot.press("p")
        await pilot.pause()

        insights = showing(app, InsightsScreen)
        assert insights.period.granularity is Granularity.DAY
        assert insights.query_one(AppHeader).context.endswith(insights.period.label)


async def test_saving_settings_redraws_insights_under_the_dialog(
    app_factory: AppFactory,
) -> None:
    """Settings can be saved with Insights on the stack, so it redraws too."""
    app = app_factory()
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        charts = showing(app, InsightsScreen).query_one(BalanceHistory)
        before = str(charts.border_subtitle)

        await pilot.press("f4")
        await pilot.pause()
        showing(app, SettingsScreen).query_one(
            "#input-working-days", Input
        ).value = "Tue-Thu"
        await pilot.click("#btn-save")
        await pilot.pause()

        charts = showing(app, InsightsScreen).query_one(BalanceHistory)
        after = str(charts.border_subtitle)
        assert after != before, f"the charts still say {before}"


async def test_settings_move_the_leave_year_under_insights(
    app_factory: AppFactory,
) -> None:
    """Every chart here is measured across the leave year the settings own."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        assert showing(app, InsightsScreen).period.start == date(2026, 4, 6)

        settings = app.services.settings
        current = settings.resolved()
        settings.save_settings(
            SettingsUpdate(
                leave_year_start=(1, 1),
                working_days=current.working_days,
                division=current.division,
                auto_close=current.auto_close,
            )
        )
        app.refresh_open_screens(Scope.SETTINGS)
        await pilot.pause()

        insights = showing(app, InsightsScreen)
        assert insights.period.start == date(2026, 1, 1)
        assert any(
            "2026" in str(header.context) for header in insights.query(AppHeader)
        ), "the header names the period it is showing"

        moved = insights.period
        app.refresh_open_screens(Scope.CLOCK)
        await pilot.pause()

        assert showing(app, InsightsScreen).period == moved, (
            "clocking redraws the charts; it does not re-read the leave year"
        )


# ---- the bento grid ----


def islands(app: FlexiApp) -> list[Module]:
    return list(app.screen.query(Module))


async def test_wide_islands_take_both_columns(
    app_factory: AppFactory,
) -> None:
    """Five islands into two columns: one reads across and the other four pair.

    An island added without a partner leaves a hole beside it.
    """
    app = app_factory()
    async with app.run_test(size=(150, 46)) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        await pilot.pause()
        placed = islands(app)

        wide = [one for one in placed if one.has_class("bento--wide")]
        assert len(wide) == 1, "one island reads across"
        assert (len(placed) - len(wide)) % 2 == 0, "the rest pair off"


async def test_island_says_how_much_room_it_needs(app_factory: AppFactory) -> None:
    """How wide a chart has to be is a fact about the chart, not the screen."""
    app = app_factory()
    async with app.run_test(size=(150, 46)) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        await pilot.pause()

        for one in islands(app):
            assert one.has_class("bento--wide") == bool(one.BENTO)


async def test_wide_island_is_wider_than_a_paired_one(
    app_factory: AppFactory,
) -> None:
    """The class has to reach the layout, not just the DOM."""
    app = app_factory()
    async with app.run_test(size=(150, 46)) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        await pilot.pause()
        placed = islands(app)

        wide = next(one for one in placed if one.has_class("bento--wide"))
        paired = next(one for one in placed if not one.has_class("bento--wide"))

        assert wide.region.width > paired.region.width * 1.5


async def test_narrow_collapses_every_island_to_one_column(
    app_factory: AppFactory,
) -> None:
    """Below the fold class the grid is one column, and the spans collapse.

    A span of two in a grid of one is a cell that reaches past the screen.
    """
    app = app_factory()
    async with app.run_test(size=(84, 30)) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        await pilot.pause()

        assert app.screen.has_class("-narrow")
        widths = {one.region.width for one in islands(app)}
        assert len(widths) == 1, f"islands should share one width, got {widths}"


async def test_every_panel_on_the_screen_has_a_jump_key(
    app_factory: AppFactory,
) -> None:
    """Both ways round, so a panel added later cannot go without a badge."""
    app = app_factory()
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        insights = showing(app, InsightsScreen)
        mounted = {module.id for module in insights.query(Module)}

        assert mounted == set(insights.jump_targets())


async def test_heatmap_follows_the_named_period(
    app_factory: AppFactory,
) -> None:
    """The heatmap and the header have to name the same year."""
    app = app_factory()
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        heatmap = app.screen.query_one(YearHeatmap)
        assert max(heatmap.ledgers) == date(2026, 6, 11)

        await pilot.press("left_square_bracket")
        await pilot.pause()

        heatmap = app.screen.query_one(YearHeatmap)
        assert min(heatmap.ledgers) == date(2025, 4, 6)
        assert max(heatmap.ledgers) == date(2026, 4, 5)


async def test_heatmap_says_a_year_has_not_started(
    app_factory: AppFactory,
) -> None:
    """Next year has no days behind it, and an empty grid looks like a bug."""
    app = app_factory()
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        await pilot.press("right_square_bracket")
        await pilot.pause()

        insights = showing(app, InsightsScreen)
        assert str(insights.query_one("#year-heatmap").border_subtitle) == "not started"
        assert insights.query_one(YearHeatmap).ledgers == {}


async def test_heatmap_draws_a_year_in_a_week_period(
    app_factory: AppFactory,
) -> None:
    """A year-shaped panel narrowed to one column answers nothing."""
    app = app_factory()
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        await pilot.press(CONFIG.hotkeys.period_cycle)
        await pilot.pause()

        heatmap = app.screen.query_one(YearHeatmap)
        assert min(heatmap.ledgers) == date(2026, 4, 6)


async def test_running_balance_names_its_span(
    app_factory: AppFactory,
) -> None:
    """The line starts at zero on the period's first day.

    Over the leave year that is the balance the dashboard shows; over a month
    it is the drift within the month, so the subtitle names which.
    """
    app = app_factory()
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        insights = showing(app, InsightsScreen)
        assert str(insights.query_one("#running-balance").border_subtitle).endswith(
            "on 11 Jun"
        )

        await pilot.press(CONFIG.hotkeys.period_cycle)
        await pilot.pause()

        insights = showing(app, InsightsScreen)
        assert insights.period.granularity is not Granularity.YEAR
        assert str(insights.query_one("#running-balance").border_subtitle).endswith(
            "this period"
        )


async def test_leave_panel_names_the_dashboard_year(
    app_factory: AppFactory,
) -> None:
    """Three panels name one span the same way; `Apr 26` is not the 6th."""
    app = app_factory()
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.press("f3")
        await pilot.pause()

        insights = showing(app, InsightsScreen)
        subtitle = str(insights.query_one("#leave-burndown").border_subtitle)
        assert subtitle == "6 Apr 26–5 Apr 27"
