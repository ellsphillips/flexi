"""Phase 3: the charts, and the rules they are meant to keep."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from flexi.components.charts import (
    Burndown,
    DivergingBars,
    WeekRibbon,
    YearHeatmap,
    week_columns,
)
from flexi.components.chrome import AppHeader
from flexi.constants import DayKind
from flexi.domain.ledger import DayLedger
from flexi.domain.period import Granularity
from flexi.screens.insights import InsightsScreen
from tests.tui.conftest import WIDE, AppFactory, screen_text, showing, status_text

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
    columns = week_columns(days)
    assert [column.label for column in columns] == ["8", "15"]
    assert columns[0].value == pytest.approx(1.0)
    assert columns[1].readout == "−2:00"


def test_week_columns_of_nothing_is_empty() -> None:
    """It has no opinion about an empty period."""
    assert week_columns([]) == []


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


async def test_the_insights_screen_reports_through_the_shared_footer(
    app_factory: AppFactory,
) -> None:
    """Every screen says things in the same place, so nobody has to look twice.

    Insights has its own footer instance, and a screen that kept its messages
    to itself would leave the status bar showing whatever the dashboard last
    said.
    """
    app = app_factory()
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.press("f3")
        await pilot.pause()

        showing(app, InsightsScreen).status("Charted to today")
        await pilot.pause()

        assert status_text(app) == "Charted to today"
