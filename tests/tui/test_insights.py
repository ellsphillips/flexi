"""Phase 3: the charts, and the rules they are meant to keep."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from textual.widgets import Input

from flexi.components.charts import (
    Burndown,
    DivergingBars,
    WeekRibbon,
    YearHeatmap,
    week_columns,
)
from flexi.components.chrome import AppHeader
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


# -- pure helpers ----------------------------------------------------------


def test_week_columns_group_days_into_weeks() -> None:
    """It buckets by the Monday, so a bar is a week whatever day it starts on."""
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
    """It has no opinion about an empty period."""
    assert week_columns([], first_weekday=0) == []


# -- the screen ------------------------------------------------------------


async def test_f3_opens_insights_on_the_leave_year(app_factory: AppFactory) -> None:
    """It opens on the year: four bars of one week is worse than the table."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        assert showing(app, InsightsScreen).period.start == date(2026, 4, 6)


async def test_escape_returns_to_the_dashboard(app_factory: AppFactory) -> None:
    """It leaves the way every pushed screen does."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, InsightsScreen)
        assert app.nav == "dashboard"


async def test_f1_returns_to_the_dashboard(app_factory: AppFactory) -> None:
    """It leaves Insights, not just relabels the nav bar.

    Insights is a pushed screen, so `f1` has to dismiss it. Setting `nav` alone
    left escape as the only way back.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        showing(app, InsightsScreen)

        await pilot.press("f1")
        await pilot.pause()
        assert not isinstance(app.screen, InsightsScreen)
        assert app.nav == "dashboard"


async def test_all_four_charts_draw(app_factory: AppFactory) -> None:
    """It renders every panel with data rather than an empty state."""
    app = app_factory()
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        for chart in (DivergingBars, Burndown, WeekRibbon, YearHeatmap):
            assert app.screen.query_one(chart)
        text = screen_text(app)
        assert "Nothing recorded yet" not in text
        assert "No entitlement recorded" not in text


async def test_the_balance_chart_stops_at_today(app_factory: AppFactory) -> None:
    """It does not chart a cliff of deficits for days nobody has lived yet."""
    app = app_factory()
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        subtitle = str(app.screen.query_one("#balance-history").border_subtitle)
        assert "11 Jun" in subtitle
        assert subtitle.startswith("+"), f"expected a surplus, got {subtitle!r}"


async def test_every_chart_writes_its_figures_as_well_as_drawing_them(
    app_factory: AppFactory,
) -> None:
    """No chart is the only way to read its own numbers."""
    app = app_factory()
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        text = screen_text(app)
        assert "best" in text
        assert "worst" in text
        assert "taken" in text
        assert "left" in text
        assert "+" in text
        assert "−" in text


async def test_the_heatmap_legend_names_both_ends(app_factory: AppFactory) -> None:
    """A diverging ramp with no labelled poles is a mood, not a scale."""
    app = app_factory()
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        legend = str(app.screen.query_one(YearHeatmap).render()).splitlines()[-1]
        assert legend.startswith("−")
        assert "+" in legend


async def test_insights_panels_are_jumpable(app_factory: AppFactory) -> None:
    """It offers the same one-key navigation the dashboard does."""
    app = app_factory()
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        insights = showing(app, InsightsScreen)
        for widget_id in insights.jump_targets():
            assert insights.query(f"#{widget_id}"), f"{widget_id} is not mounted"


# -- moving the period -----------------------------------------------------


async def test_a_leave_year_that_has_not_begun_says_so_rather_than_drawing_nothing(
    app_factory: AppFactory,
) -> None:
    """Next year has no weeks behind it, and an empty chart looks like a bug.

    The balance chart stops at today, so in a period that starts after today
    there is nothing between the two — which is a sentence, not a blank panel
    somebody has to work out for themselves.
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


async def test_today_brings_the_charts_back_to_the_year_being_lived(
    app_factory: AppFactory,
) -> None:
    """One key back from wherever the arrows have got to."""
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


async def test_cycling_the_period_re_labels_the_header_as_well_as_the_charts(
    app_factory: AppFactory,
) -> None:
    """The charts are only readable against the span they cover.

    Zooming redraws every panel, and a header still claiming the leave year
    would be describing data that had moved out from under it.
    """
    app = app_factory()
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        # Zoomed out of next year rather than this one: `p` narrows a leave year
        # to a single day, and `DivergingBars._arms` divides by `high + low` --
        # zero whenever every bar is the same value, which one bar always is. A
        # year with no weeks behind it charts nothing, so it survives the zoom.
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
    """The third screen the application can be showing when settings are saved.

    It had no `refresh_modules` at all, and the app looked for the dashboard
    and redrew only that — so a new working pattern left every chart on this
    screen measured against the one it replaced.
    """
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


async def test_saving_settings_moves_the_leave_year_under_open_insights(
    app_factory: AppFactory,
) -> None:
    """Every chart here is measured across the leave year the settings own.

    This screen had no `refresh_modules` at all until the app started treating
    the whole stack alike, so a settings change behind it left every figure
    computed against the year that had just been replaced.
    """
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
