"""Insights: how the balance and the allowances actually moved.

Four questions, four forms, and each form was chosen because the data has that
job. Nothing here is a chart for the sake of having one — the dashboard already
answers "where am I"; this answers "how did I get here".
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen

from flexi.components.charts import (
    Burndown,
    DivergingBars,
    WeekRibbon,
    YearHeatmap,
    week_columns,
)
from flexi.components.chrome import AppFooter, AppHeader
from flexi.components.common import Tone, mark_width
from flexi.components.modules.base import Module
from flexi.config import CONFIG
from flexi.constants import AbsenceType
from flexi.domain.format import delta, hm
from flexi.domain.period import Granularity, Period
from flexi.messages import Scope
from flexi.services.registry import Services

RIBBON_DAYS = 21
"""Three weeks of strips. Enough to see a pattern, few enough to fit above the
fold beside three other panels."""


class BalanceHistory(Module):
    """Week by week, what the balance did."""

    WATCHES: ClassVar[Scope] = Scope.ALL

    def __init__(self, **kwargs: Any) -> None:
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
        self.query_one("#balance-bars", DivergingBars).show(week_columns(ledgers))
        total = self.services.ledger.summary(period.start, end, now=self.now)
        self.set_subtitle(f"{delta(total.delta)} to {end.strftime('%-d %b')}")


class LeaveBurndown(Module):
    """Annual leave spent against the pace an even spread would set."""

    WATCHES: ClassVar[Scope] = Scope.ABSENCE | Scope.SETTINGS | Scope.PERIOD

    def __init__(self, **kwargs: Any) -> None:
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

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(id="week-ribbon", title="Shape of the days", **kwargs)

    def compose(self) -> ComposeResult:
        yield WeekRibbon(id="ribbon")

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
            ledgers[-RIBBON_DAYS:], self.services.ledger.window
        )
        self.set_subtitle(f"to {end.strftime('%-d %b')}")


class YearAtAGlance(Module):
    """Every day of the leave year, coloured by how it went."""

    WATCHES: ClassVar[Scope] = Scope.ALL

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(id="year-heatmap", title="The leave year", **kwargs)

    def compose(self) -> ComposeResult:
        yield YearHeatmap(id="heatmap")

    def on_mount(self) -> None:
        self.rebuild()

    def rebuild(self) -> None:
        today = self.now.date()
        start, _ = self.services.absence.leave_year_bounds(today)
        ledgers = self.services.ledger.days(start, today, now=self.now)
        self.query_one("#heatmap", YearHeatmap).show(ledgers)
        worked = sum((item.worked for item in ledgers), start=timedelta())
        self.set_subtitle(f"{hm(worked)} worked")


class InsightsScreen(Screen[None]):
    """The four questions the dashboard does not answer."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding(CONFIG.hotkeys.today, "today", "Today", show=True),
        Binding(CONFIG.hotkeys.period_prev, "shift(-1)", "Previous", show=False),
        Binding(CONFIG.hotkeys.period_next, "shift(1)", "Next", show=False),
        Binding(CONFIG.hotkeys.period_cycle, "cycle", "Period", show=True),
        Binding("escape", "back", "Back", show=True),
    ]

    def __init__(self, services: Services, period: Period, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._services = services
        # Opens on the leave year rather than inheriting a week: a chart of one
        # week's four bars is a worse answer than the table it came from.
        self.period = period.zoom(Granularity.YEAR)
        self.now = datetime.now()

    def compose(self) -> ComposeResult:
        yield AppHeader()
        with VerticalScroll(id="insights-body"):
            with Horizontal(classes="insights-row"):
                yield BalanceHistory()
                yield LeaveBurndown()
            with Vertical(classes="insights-row"):
                yield ShapeOfTheWeeks()
                yield YearAtAGlance()
        yield AppFooter()

    def on_mount(self) -> None:
        for header in self.query(AppHeader):
            header.set_active("insights")
            header.context = f"{date.today().strftime('%a %-d %b')} · {self.period.label}"

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

    def set_period(self, period: Period) -> None:
        self.period = period
        for header in self.query(AppHeader):
            header.context = f"{date.today().strftime('%a %-d %b')} · {period.label}"
        self._services.invalidate()
        for module in self.query(Module):
            module.rebuild()

    def action_today(self) -> None:
        self.set_period(self.period.go_to(date.today()))

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

    def status(self, message: str, tone: Tone = Tone.NEUTRAL) -> None:
        for footer in self.query(AppFooter):
            footer.set_status(message, tone)
