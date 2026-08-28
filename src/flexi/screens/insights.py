"""Insights: how the balance and the allowances actually moved.

Four questions, four forms, and each form was chosen because the data has that
job. Nothing here is a chart for the sake of having one — the dashboard already
answers "where am I"; this answers "how did I get here".
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
from flexi.domain.format import day_month, delta, hm
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
"""Three weeks of strips. Enough to see a pattern, few enough to fit above the
fold beside three other panels."""


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
        # Stop at today. Every working day after it expects hours and has none
        # recorded, so charting the rest of a leave year draws a cliff of
        # deficits for days nobody has lived yet.
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

    The figure on the dashboard is one number and this is where it came from.
    A weekly total cannot show it: a contract is the promise that those barely
    move, so charting them draws three near-identical slabs and calls it a
    trend. The balance is the accumulation of the differences, which is the
    thing that actually wanders.

    Zero is a rule rather than a series. It is not a reading somebody took; it
    is the line the readings are on one side of or the other, and it is what
    turns a wandering line into "ahead" and "behind".
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
        # Stop at today. Every working day after it expects hours and has none
        # recorded, so carrying on draws a cliff into a debt nobody has run up.
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
        self.set_subtitle(f"{delta(timedelta(hours=running[-1]))} on {day_month(end)}")


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
        self.set_subtitle(f"{start.strftime('%b %y')}–{end.strftime('%b %y')}")


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
        today = self.now.date()
        start, _ = self.services.absence.leave_year_bounds(today)
        ledgers = self.services.ledger.days(start, today, now=self.now)
        self.query_one("#heatmap", YearHeatmap).show(
            ledgers, first_weekday=self.period.first_weekday
        )
        worked = sum((item.worked for item in ledgers), start=timedelta())
        self.set_subtitle(f"{hm(worked)} worked")


class InsightsScreen(Screen[None]):
    """The four questions the dashboard does not answer."""

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
        # Opens on the leave year rather than inheriting a week: a chart of one
        # week's four bars is a worse answer than the table it came from.
        self.period = period.zoom(Granularity.YEAR)
        self.now = wallclock.now()

    def compose(self) -> ComposeResult:
        yield AppHeader()
        with VerticalScroll(id="insights-body"):
            # Ordered by what a reader wants first, not by size. The balance is
            # the headline; the two beside each other are the two allowances it
            # is spent against; the shapes underneath are the detail behind it.
            # One island reads across the full width and the other four pair
            # off, so no cell of the grid is left empty. A row is as tall as its
            # tallest island, so each is placed beside one of about its height.
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
        return {
            "balance-history": "b",
            "leave-burndown": "l",
            "week-ribbon": "s",
            "year-heatmap": "y",
        }

    # -- period ------------------------------------------------------------

    def refresh_modules(self, scope: Scope) -> None:
        """Redraw on an external change, so the app can treat every screen alike.

        `LeaveScreen` said that and the app called it on neither, singling the
        dashboard out instead; this screen did not have the method at all.
        """
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
        # No `invalidate()`: moving the view changes no rows, and the ledger
        # cache is what stops a leave year being re-derived from scratch on
        # every keypress. `DashboardScreen.refresh_modules` states the same rule
        # -- `Scope.PERIOD` is "the temporal view moved" -- and this screen was
        # dropping 371 day ledgers to redraw with the same numbers.
        for module in self.query(Module):
            module.rebuild_if(Scope.PERIOD)

    def action_today(self) -> None:
        self.set_period(self.period.go_to(wallclock.today()))

    def action_shift(self, count: int) -> None:
        self.set_period(self.period.shift(count))

    def action_cycle(self) -> None:
        self.set_period(self.period.zoom(self.period.granularity.next()))

    def action_back(self) -> None:
        """Dismiss, rather than pop.

        `pop_screen` removes the screen without running the callback that
        `push_screen` was given, so the nav bar would keep pointing at Insights
        after the user had left it.
        """
        self.dismiss(None)
