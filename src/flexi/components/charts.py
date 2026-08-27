"""Charts, drawn as characters.

Series colour comes from the three validated slots only -- TOIL, annual, sick --
and a fourth series folds into a neutral. Every chart writes its figure beside
the mark, so none of them is the only way to read its own numbers.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, ClassVar, Final

from rich.style import Style
from rich.text import Text
from textual.app import RenderResult
from textual.widget import Widget

from flexi.components.punch import PUNCH_CLASSES, render_strip
from flexi.domain.dates import week_start
from flexi.domain.format import MINUS, delta, hm
from flexi.domain.format import days as fmt_days
from flexi.domain.ledger import DayLedger
from flexi.domain.punch import Window
from flexi.domain.stitch import weekday_initials

__all__ = (
    "BASELINE",
    "BLOCK",
    "DIVERGING_STEPS",
    "EMPTY",
    "FULL",
    "HEAT",
    "Burndown",
    "Column",
    "DivergingBars",
    "WeekRibbon",
    "YearHeatmap",
    "week_columns",
)

BLOCK: Final = "█"
BASELINE: Final = "─"
"""Whole cells only, both arms.

An earlier draft drew eighths, which reads beautifully upward — `▁▂▃▄▅▆▇` are
everywhere — and needs U+1FB0x Symbols for Legacy Computing to do the same
downward. Those are missing from most terminal fonts, so half the chart rendered
as tofu on the machines it was drawn for. Whole cells cost a quarter of a bar's
precision and the exact figure is printed underneath anyway."""
FULL: Final = "█"
HEAT: Final = "■"
EMPTY: Final = "·"

DIVERGING_STEPS: Final = 4
"""Steps per arm of the heatmap ramp. Four is as many as a reader can rank by
eye without a legend they have to keep consulting."""


@dataclass(frozen=True, slots=True)
class Column:
    """One bar: a label, a signed value, and the figure to write beside it."""

    label: str
    value: float
    readout: str = ""


class DivergingBars(Widget):
    """A signed series around a zero line.

    Two hues and a neutral baseline, which is the whole grammar of a diverging
    chart: a bar above the line means one thing and a bar below means its
    opposite, and the reader never has to look up which colour is which because
    the side of the line already said it.
    """

    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "chart--surplus",
        "chart--deficit",
        "chart--baseline",
        "chart--label",
        "chart--figure",
    }

    def __init__(self, *, height: int = 7, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.columns: tuple[Column, ...] = ()
        self.rows = max(3, height)

    def show(self, columns: list[Column]) -> None:
        self.columns = tuple(columns)
        self.refresh()

    def render(self) -> RenderResult:
        label = self.get_component_rich_style("chart--label")
        if not self.columns:
            return Text("Nothing recorded yet", style=label)

        shown, gap = self._fit()
        up, down = self._arms(shown)
        surplus = self.get_component_rich_style("chart--surplus")
        deficit = self.get_component_rich_style("chart--deficit")

        lines = [
            self._band(shown, gap, level, up, self._high(shown), surplus, above=True)
            for level in range(up, 0, -1)
        ]
        lines.append(self._baseline(shown, gap))
        lines.extend(
            self._band(shown, gap, level, down, self._low(shown), deficit, above=False)
            for level in range(1, down + 1)
        )
        lines.append(self._caption(shown))
        return Text("\n").join(lines)

    def _arms(self, shown: tuple[Column, ...]) -> tuple[int, int]:
        """How many rows each side of the baseline gets.

        Split in proportion to the data, not down the middle. A series with no
        deficit weeks does not need four rows of empty negative axis, and a
        series that is all deficit should not be squashed into two.
        """
        rows = max(2, self.rows - 1)
        high, low = self._high(shown), self._low(shown)
        if not low:
            return rows - 1, 1
        if not high:
            return 1, rows - 1
        share = high / (high + low)
        up = max(1, min(rows - 1, round(share * rows)))
        return up, rows - up

    @staticmethod
    def _high(shown: tuple[Column, ...]) -> float:
        """How far the surplus reaches above the baseline. Never below it.

        Clamped at zero, because it is a distance from the baseline rather than
        the largest value. Without the clamp a week of nothing but deficit gave
        a *negative* height for the surplus arm, and `_arms` divided by
        `high + low` — which for a single column is `value + -value`, exactly
        zero. Opening Insights on a first install, where there is one week and
        it is behind, was a ZeroDivisionError.
        """
        return max(0.0, max((column.value for column in shown), default=0.0))

    @staticmethod
    def _low(shown: tuple[Column, ...]) -> float:
        """How far the deficit reaches below the baseline. Never above it."""
        return max(0.0, -min((column.value for column in shown), default=0.0))

    def _fit(self) -> tuple[tuple[Column, ...], int]:
        """The bars that fit, most recent first, and whether they get a gap.

        Trimmed from the *left*: a year of weeks will not fit a half-width panel,
        and the weeks worth dropping are the oldest.
        """
        width = max(1, self.content_size.width)
        if len(self.columns) * 2 <= width:
            return self.columns, 1
        return self.columns[-width:], 0

    def _band(
        self,
        shown: tuple[Column, ...],
        gap: int,
        level: int,
        arm: int,
        extent: float,
        style: Style,
        *,
        above: bool,
    ) -> Text:
        """One row of the chart, at a fixed distance from the baseline."""
        text = Text(no_wrap=True, end="")
        span = extent or 1.0
        for column in shown:
            value = column.value if above else -column.value
            reach = max(0.0, value) / span * arm
            text.append(BLOCK if reach >= level - 0.5 else " ", style)
            text.append(" " * gap)
        return text

    def _baseline(self, shown: tuple[Column, ...], gap: int) -> Text:
        style = self.get_component_rich_style("chart--baseline")
        return Text(BASELINE * (len(shown) * (1 + gap)), style=style, no_wrap=True)

    def _caption(self, shown: tuple[Column, ...]) -> Text:
        """The extremes, named. A bar chart nobody can read a value off is a mood.

        Direct-labelling every bar would be unreadable at 52 weeks, so the two
        that matter are labelled and the rest are shape.
        """
        label = self.get_component_rich_style("chart--label")
        if not shown:
            return Text("", style=label)
        best = max(shown, key=lambda item: item.value)
        worst = min(shown, key=lambda item: item.value)
        if best is worst:
            return Text(f"{best.label}: {best.readout}", style=label)
        return Text(
            f"best {best.label} {best.readout} · worst {worst.label} {worst.readout}",
            style=label,
        )


class Burndown(Widget):
    """One series against a reference line.

    A single series needs no legend — the title names it — and the reference is
    drawn as a rule rather than a second series, because "where you should be"
    is not a thing that happened.
    """

    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "chart--series",
        "chart--reference",
        "chart--label",
        "chart--figure",
    }

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.remaining: float | None = None
        self.total: float = 0.0
        self.pace: float | None = None

    def show(self, remaining: float | None, total: float, pace: float | None) -> None:
        self.remaining, self.total, self.pace = remaining, total, pace
        self.refresh()

    def render(self) -> RenderResult:
        label = self.get_component_rich_style("chart--label")
        if self.remaining is None or self.total <= 0:
            return Text("No entitlement recorded", style=label)

        width = max(12, self.content_size.width)
        spent = self.total - self.remaining
        track = [EMPTY] * width
        filled = int(round(spent / self.total * width))
        for index in range(min(filled, width)):
            track[index] = FULL

        marker = (
            None
            if self.pace is None
            else min(width - 1, max(0, int(round(self.pace / self.total * width))))
        )
        if marker is not None:
            track[marker] = "┃"

        # Glyphs first, then spans: rebuilding a Text to swap a character drops
        # the base style it was built with.
        text = Text("".join(track))
        text.stylize(self.get_component_rich_style("chart--series"), 0, width)
        if marker is not None:
            text.stylize(
                self.get_component_rich_style("chart--reference"), marker, marker + 1
            )

        text.append("\n")
        text.append(
            f"{fmt_days(spent)} taken · {fmt_days(self.remaining)} left"
            f" · pace {fmt_days(round(self.pace or 0, 1))}",
            label,
        )
        return text


class WeekRibbon(Widget):
    """Punch strips stacked on one time axis, a week to a row.

    The signature element scaled up. A table of weekly totals says how much; this
    says *when*, and the shape of a month of mornings is not something a column
    of numbers can show.
    """

    COMPONENT_CLASSES: ClassVar[set[str]] = {*PUNCH_CLASSES, "chart--label"}

    def __init__(
        self, *, window: Window | None = None, now: datetime, **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self.ledgers: tuple[DayLedger, ...] = ()
        self.window = window or Window()
        self.now = now

    def show(
        self,
        ledgers: list[DayLedger],
        window: Window | None = None,
        *,
        now: datetime,
    ) -> None:
        self.ledgers = tuple(ledgers)
        if window is not None:
            self.window = window
        self.now = now
        self.refresh()

    def render(self) -> RenderResult:
        label = self.get_component_rich_style("chart--label")
        if not self.ledgers:
            return Text("Nothing recorded yet", style=label)

        gutter = 8
        width = max(12, self.content_size.width - gutter)
        lines: list[Text] = []
        for ledger in self.ledgers:
            row = Text(f"{ledger.date.strftime('%a %d')}".ljust(gutter), style=label)
            row.append(
                render_strip(
                    ledger,
                    width,
                    self.window,
                    self.get_component_rich_style,
                    now=self.now,
                )
            )
            lines.append(row)
        return Text("\n").join(lines)


class YearHeatmap(Widget):
    """A calendar grid coloured by how each day went.

    Weekday down, week across — the shape every contribution graph uses, because
    it puts "my Fridays are short" and "March was heavy" in the same picture.

    Colour carries magnitude on a diverging ramp; the *glyph* carries day type,
    so the two encodings never fight over the same cell.
    """

    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "chart--label",
        "chart--neutral",
        *(f"chart--surplus-{step}" for step in range(1, DIVERGING_STEPS + 1)),
        *(f"chart--deficit-{step}" for step in range(1, DIVERGING_STEPS + 1)),
    }

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.ledgers: dict[date, DayLedger] = {}
        self.scale = timedelta(hours=2)
        self.first_weekday = 0
        """Which day the rows start on. Set by `show`; initialised because
        `render` can run before the first one."""

    def show(self, ledgers: list[DayLedger], *, first_weekday: int) -> None:
        self.ledgers = {item.date: item for item in ledgers}
        self.first_weekday = first_weekday
        worst = max(
            (abs(item.balance_effect) for item in ledgers), default=timedelta(hours=2)
        )
        # A floor on the scale: without one, a fortnight of near-perfect days
        # would be drawn as violently as a fortnight of disasters.
        self.scale = max(worst, timedelta(hours=2))
        self.refresh()

    def render(self) -> RenderResult:
        label = self.get_component_rich_style("chart--label")
        if not self.ledgers:
            return Text("Nothing recorded yet", style=label)

        # Both the grid and its row labels take the configured first day. They
        # were a hardcoded Monday and a hardcoded "MTWTFSS" while the bars
        # above them, on the same screen, honoured the setting -- so a
        # Sunday-first week put the two charts a day out of step with each
        # other and with the calendar on the leave screen.
        start = week_start(min(self.ledgers), first_weekday=self.first_weekday)
        end = max(self.ledgers)
        weeks = ((end - start).days // 7) + 1

        lines: list[Text] = []
        for weekday, initial in enumerate(weekday_initials(self.first_weekday)):
            row = Text(f"{initial} ", style=label)
            for week in range(weeks):
                when = start + timedelta(weeks=week, days=weekday)
                row.append(*self._cell(when))
            lines.append(row)
        lines.append(self._legend())
        return Text("\n").join(lines)

    def _cell(self, when: date) -> tuple[str, Style]:
        ledger = self.ledgers.get(when)
        if ledger is None:
            return " ", Style()
        if not ledger.is_working_day or ledger.is_holiday:
            return EMPTY, self.get_component_rich_style("chart--neutral")
        effect = ledger.balance_effect
        if effect == timedelta():
            return HEAT, self.get_component_rich_style("chart--neutral")
        share = min(1.0, abs(effect) / self.scale)
        step = max(1, min(DIVERGING_STEPS, int(round(share * DIVERGING_STEPS))))
        arm = "surplus" if effect > timedelta() else "deficit"
        return HEAT, self.get_component_rich_style(f"chart--{arm}-{step}")

    def _legend(self) -> Text:
        """Never colour alone: the ramp is drawn with its two ends named."""
        label = self.get_component_rich_style("chart--label")
        text = Text(f"{MINUS}{hm(self.scale)} ", style=label)
        for step in range(DIVERGING_STEPS, 0, -1):
            text.append(HEAT, self.get_component_rich_style(f"chart--deficit-{step}"))
        text.append(HEAT, self.get_component_rich_style("chart--neutral"))
        for step in range(1, DIVERGING_STEPS + 1):
            text.append(HEAT, self.get_component_rich_style(f"chart--surplus-{step}"))
        text.append(f" +{hm(self.scale)}", label)
        return text


def week_columns(ledgers: list[DayLedger], *, first_weekday: int) -> list[Column]:
    """Group a run of days into one bar per week, for :class:`DivergingBars`.

    ``first_weekday`` because these bars sit on the same screen as a calendar
    drawn from it. Taking the default, they bucketed on Mondays and labelled
    each bar with a Monday's date while everything else on the configuration
    started the week on Sunday.
    """
    buckets: defaultdict[date, timedelta] = defaultdict(timedelta)
    for ledger in ledgers:
        buckets[week_start(ledger.date, first_weekday=first_weekday)] += (
            ledger.balance_effect
        )
    return [
        Column(
            label=str(week.day),
            value=total.total_seconds() / 3600,
            readout=delta(total),
        )
        for week, total in sorted(buckets.items())
    ]
