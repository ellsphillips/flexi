"""Insights: how the balance and the allowances actually moved.

The dashboard answers "where am I"; this screen answers "how did I get here",
with a form per question chosen to suit the data behind it.
"""

from __future__ import annotations

from datetime import timedelta
from typing import ClassVar, Unpack

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import VerticalScroll
from textual.screen import Screen

from flexi import wallclock
from flexi.components.charts import (
    Burndown,
    DivergingBars,
    WeekRibbon,
    YearHeatmap,
    running_balance,
    week_columns,
)
from flexi.components.chrome import AppFooter, AppHeader
from flexi.components.common import mark_width
from flexi.components.modules.base import Module
from flexi.components.options import ModuleOptions, ScreenOptions
from flexi.components.plot import Plot
from flexi.config import CONFIG
from flexi.constants import AbsenceType, Granularity
from flexi.context import service_app
from flexi.domain.format import day_month, delta, hm, stamp
from flexi.domain.period import Period
from flexi.domain.plot import Mark, Series
from flexi.messages import Scope

__all__ = (
    "RIBBON_DAYS",
    "BalanceHistory",
    "InsightsScreen",
    "LeaveBurndown",
    "RunningBalance",
    "ShapeOfTheWeeks",
    "YearAtAGlance",
)

RIBBON_DAYS = 21
"""Three weeks of strips: enough to see a pattern, few enough to fit beside
three other panels."""


class BalanceHistory(Module):
    """Week by week, what the balance did."""

    WATCHES: ClassVar[Scope] = Scope.ALL

    def __init__(self, **kwargs: Unpack[ModuleOptions]) -> None:
        super().__init__(id="balance-history", title="Balance by week", **kwargs)

    def compose(self) -> ComposeResult:
        yield DivergingBars(height=9, id="balance-bars")

    def on_mount(self) -> None:
        self.rebuild()

    def rebuild(self) -> None:
        period = self.period
        # Stop at today: a working day in the future expects hours and has none
        # recorded, so charting past it draws a cliff of deficits.
        end = min(period.end, self.now.date())
        if end < period.start:
            self.query_one("#balance-bars", DivergingBars).show([])
            self.set_subtitle("not started")
            return
        ledgers = self.services.ledger.days(period.start, end, now=self.now)
        self.query_one("#balance-bars", DivergingBars).show(
            week_columns(ledgers, first_weekday=period.first_weekday)
        )
        total = self.services.ledger.summary(period.start, end, now=self.now)
        self.set_subtitle(f"{delta(total.delta)} to {day_month(end)}")


class RunningBalance(Module):
    """The flexi balance, day by day, and which side of zero it has been.

    The balance accumulates the daily differences, so it moves where a weekly
    total does not. Zero is drawn as a rule, not a series: it is the line the
    readings sit one side of, and it turns a wandering line into "ahead" and
    "behind".
    """

    WATCHES: ClassVar[Scope] = Scope.ALL

    BENTO = "bento--wide"
    """A time axis: every column it loses is days of it."""

    def __init__(self, **kwargs: Unpack[ModuleOptions]) -> None:
        super().__init__(id="running-balance", title="Running balance", **kwargs)

    def compose(self) -> ComposeResult:
        yield Plot(id="balance-plot")

    def on_mount(self) -> None:
        self.rebuild()

    def rebuild(self) -> None:
        period = self.period
        # Stop at today: a working day in the future expects hours and has none
        # recorded, so carrying on draws a cliff into a debt no one has run up.
        end = min(period.end, self.now.date())
        chart = self.query_one("#balance-plot", Plot)
        if end < period.start:
            chart.show([], empty_message="Not started")
            self.set_subtitle("not started")
            return

        ledgers = self.services.ledger.days(period.start, end, now=self.now)
        running = running_balance(ledgers)
        chart.show(
            [Series("balance", running, Mark.LINE, "series")],
            rule=0.0,
            empty_message="Nothing recorded yet",
        )
        # The line starts at zero on the period's first day: over the leave year
        # that is the balance, over a month only the drift within the month, so
        # the two are captioned differently.
        total = delta(timedelta(hours=running[-1]))
        if period.granularity is Granularity.YEAR:
            self.set_subtitle(f"{total} on {day_month(end)}")
        else:
            self.set_subtitle(f"{total} this period")


class LeaveBurndown(Module):
    """Annual leave spent against the pace an even spread would set."""

    WATCHES: ClassVar[Scope] = Scope.ABSENCE | Scope.SETTINGS | Scope.PERIOD

    def __init__(self, **kwargs: Unpack[ModuleOptions]) -> None:
        super().__init__(id="leave-burndown", title="Annual leave", **kwargs)

    def compose(self) -> ComposeResult:
        yield Burndown(id="leave-bar")

    def on_mount(self) -> None:
        self.rebuild()

    def rebuild(self) -> None:
        period = self.period
        data = self.services.wallet.compute(
            period.start, period.end, today=self.now.date(), now=self.now
        )
        annual = data.allowance(AbsenceType.ANNUAL)
        self.query_one("#leave-bar", Burndown).show(
            annual.remaining, annual.total or 0.0, annual.pace
        )
        start, end = data.leave_year
        # The same span the dashboard's Balance panel names, said the same way:
        # a leave year that starts on the 6th is not "Apr 26".
        self.set_subtitle(f"{stamp(start, '%-d %b %y')}–{stamp(end, '%-d %b %y')}")


class ShapeOfTheWeeks(Module):
    """The punch strip, stacked. Where the hours actually fell."""

    WATCHES: ClassVar[Scope] = Scope.ALL

    def __init__(self, **kwargs: Unpack[ModuleOptions]) -> None:
        super().__init__(id="week-ribbon", title="Shape of the days", **kwargs)

    def compose(self) -> ComposeResult:
        yield WeekRibbon(id="ribbon", now=self.now)

    def on_mount(self) -> None:
        self.rebuild()

    def rebuild(self) -> None:
        end = min(self.period.end, self.now.date())
        start = end - timedelta(days=RIBBON_DAYS - 1)
        ledgers = [
            item
            for item in self.services.ledger.days(start, end, now=self.now)
            if item.is_working_day or item.segments
        ]
        self.query_one("#ribbon", WeekRibbon).show(
            ledgers[-RIBBON_DAYS:], self.services.ledger.window, now=self.now
        )
        self.set_subtitle(f"to {day_month(end)}")


class YearAtAGlance(Module):
    """Every day of the leave year, coloured by how it went."""

    WATCHES: ClassVar[Scope] = Scope.ALL

    def __init__(self, **kwargs: Unpack[ModuleOptions]) -> None:
        super().__init__(id="year-heatmap", title="The leave year", **kwargs)

    def compose(self) -> ComposeResult:
        yield YearHeatmap(id="heatmap")

    def on_mount(self) -> None:
        self.rebuild()

    def rebuild(self) -> None:
        # The leave year the period is in, whatever the period has been zoomed
        # to, so the panel and the header name the same year.
        year = self.period.zoom(Granularity.YEAR)
        end = min(year.end, self.now.date())
        heatmap = self.query_one("#heatmap", YearHeatmap)
        if end < year.start:
            heatmap.show([], first_weekday=self.period.first_weekday)
            self.set_subtitle("not started")
            return
        ledgers = self.services.ledger.days(year.start, end, now=self.now)
        heatmap.show(ledgers, first_weekday=self.period.first_weekday)
        worked = sum((item.worked for item in ledgers), start=timedelta())
        self.set_subtitle(f"{hm(worked)} worked")


class InsightsScreen(Screen[None]):
    """The five questions the dashboard does not answer."""

    HELP_LABEL = "Insights"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding(CONFIG.hotkeys.today, "today", "Today", show=True),
        Binding(CONFIG.hotkeys.period_prev, "shift(-1)", "Previous", show=False),
        Binding(CONFIG.hotkeys.period_next, "shift(1)", "Next", show=False),
        Binding(CONFIG.hotkeys.period_cycle, "cycle", "Period", show=True),
        Binding("escape", "back", "Back", show=True),
    ]

    def __init__(self, period: Period, **kwargs: Unpack[ScreenOptions]) -> None:
        super().__init__(**kwargs)
        # Opens on the leave year, not the period it inherits: one week is four
        # bars, which the table it came from already showed better.
        self.period = period.zoom(Granularity.YEAR)
        self.now = wallclock.now()

    def compose(self) -> ComposeResult:
        yield AppHeader()
        with VerticalScroll(id="insights-body"):
            # Order is layout as well as emphasis: one island reads across the
            # full width and the other four pair off, so no grid cell is left
            # empty. A row is as tall as its tallest island, so each sits beside
            # one of about its own height.
            yield RunningBalance()
            yield ShapeOfTheWeeks()
            yield BalanceHistory()
            yield YearAtAGlance()
            yield LeaveBurndown()
        yield AppFooter()

    def on_mount(self) -> None:
        for header in self.query(AppHeader):
            header.set_active("insights")
            header.context = self.period.label

    def on_resize(self) -> None:
        mark_width(self, self.size.width)

    def jump_targets(self) -> dict[str, str]:
        """The jump key for every panel on this screen."""
        return {
            "running-balance": "r",
            "balance-history": "b",
            "leave-burndown": "l",
            "week-ribbon": "s",
            "year-heatmap": "y",
        }

    # period ----------------------------------------------------------------

    def refresh_modules(self, scope: Scope) -> None:
        """Redraw on an external change, so the app can treat every screen alike."""
        if scope & Scope.SETTINGS:
            self.period = self.period.with_year_start(
                service_app(self.app).services.settings.get_leave_year_start()
            )
            for header in self.query(AppHeader):
                header.context = self.period.label
        for module in self.query(Module):
            module.rebuild_if(scope)

    def set_period(self, period: Period) -> None:
        self.period = period
        for header in self.query(AppHeader):
            header.context = period.label
        # No `invalidate()`: `Scope.PERIOD` means the temporal view moved, which
        # changes no rows, and the ledger cache is what stops a leave year being
        # re-derived from scratch on every keypress.
        for module in self.query(Module):
            module.rebuild_if(Scope.PERIOD)

    def action_today(self) -> None:
        self.set_period(self.period.go_to(wallclock.today()))

    def action_shift(self, count: int) -> None:
        self.set_period(self.period.shift(count))

    def action_cycle(self) -> None:
        self.set_period(self.period.zoom(self.period.granularity.next()))

    def action_back(self) -> None:
        """Dismiss the screen.

        `pop_screen` would remove it without running the callback `push_screen`
        was given, leaving the nav bar pointing at Insights.
        """
        self.dismiss(None)
