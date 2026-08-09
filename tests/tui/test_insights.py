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
from flexi.constants import DayKind
from flexi.domain.ledger import DayLedger
from flexi.screens.insights import InsightsScreen
from tests.tui.conftest import WIDE, screen_text

pytestmark = pytest.mark.usefixtures("_frozen")

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


async def test_f3_opens_insights_on_the_leave_year(app_factory) -> None:
    """It opens on the year: four bars of one week is worse than the table."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        assert isinstance(app.screen, InsightsScreen)
        assert app.screen.period.start == date(2026, 4, 6)


async def test_escape_returns_to_the_dashboard(app_factory) -> None:
    """It leaves the way every pushed screen does."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, InsightsScreen)
        assert app.nav == "dashboard"


async def test_f1_returns_to_the_dashboard(app_factory) -> None:
    """It leaves Insights, not just relabels the nav bar.

    Insights is a pushed screen, so `f1` has to dismiss it. Setting `nav` alone
    left escape as the only way back.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        assert isinstance(app.screen, InsightsScreen)

        await pilot.press("f1")
        await pilot.pause()
        assert not isinstance(app.screen, InsightsScreen)
        assert app.nav == "dashboard"


async def test_all_four_charts_draw(app_factory) -> None:
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


async def test_the_balance_chart_stops_at_today(app_factory) -> None:
    """It does not chart a cliff of deficits for days nobody has lived yet."""
    app = app_factory()
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        subtitle = str(app.screen.query_one("#balance-history").border_subtitle)
        assert "11 Jun" in subtitle
        assert subtitle.startswith("+"), f"expected a surplus, got {subtitle!r}"


async def test_every_chart_writes_its_figures_as_well_as_drawing_them(
    app_factory,
) -> None:
    """No chart is the only way to read its own numbers."""
    app = app_factory()
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        text = screen_text(app)
        assert "best" in text and "worst" in text  # diverging bars
        assert "taken" in text and "left" in text  # burndown
        assert "+" in text and "−" in text  # heatmap legend, both ends named


async def test_the_heatmap_legend_names_both_ends(app_factory) -> None:
    """A diverging ramp with no labelled poles is a mood, not a scale."""
    app = app_factory()
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        legend = str(app.screen.query_one(YearHeatmap).render()).splitlines()[-1]
        assert legend.startswith("−")
        assert "+" in legend


async def test_insights_panels_are_jumpable(app_factory) -> None:
    """It offers the same one-key navigation the dashboard does."""
    app = app_factory()
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        targets = app.screen.jump_targets()
        for widget_id in targets:
            assert app.screen.query(f"#{widget_id}"), f"{widget_id} is not mounted"
