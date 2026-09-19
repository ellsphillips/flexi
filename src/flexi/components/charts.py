"""Charts, drawn as characters.

Series colour comes from the three validated slots (TOIL, annual, sick); a
fourth series folds into the neutral. Every chart writes its figure beside the
mark, so colour is never the only encoding.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from itertools import accumulate
from typing import ClassVar, Final, Unpack

from rich.style import Style
from rich.text import Text
from textual.widget import Widget

from flexi.components.options import WidgetOptions
from flexi.components.punch import PUNCH_CLASSES, render_strip
from flexi.domain.dates import week_start
from flexi.domain.format import MINUS, delta, hm, signed_days
from flexi.domain.format import days as fmt_days
from flexi.domain.ledger import DayLedger
from flexi.domain.punch import Window
from flexi.domain.stitch import weekday_initials

__all__ = (
    "AMENDED_HEAT",
    "BASELINE",
    "BLOCK",
    "DIVERGING_STEPS",
    "EMPTY",
    "FULL",
    "HEAT",
    "SECONDS_PER_HOUR",
    "Burndown",
    "Column",
    "DivergingBars",
    "WeekRibbon",
    "YearHeatmap",
    "running_balance",
    "week_columns",
)

BLOCK: Final = "█"
"""The bar cell. Whole cells only: most terminal fonts lack the downward eighths."""
BASELINE: Final = "─"
FULL: Final = "█"
HEAT: Final = "■"
AMENDED_HEAT: Final = "▒"
"""The fill, shared with the punch strips, for work written down after the fact."""
EMPTY: Final = "·"

DIVERGING_STEPS: Final = 4
"""Steps per arm of the heatmap ramp; four is as many as the eye can rank."""


SECONDS_PER_HOUR: Final = 3600


@dataclass(frozen=True, slots=True)
class Column:
    """A bar: a label, a signed value, and the figure written beside it."""

    label: str
    value: float
    readout: str = ""


class DivergingBars(Widget):
    """A signed series around a zero line: two hues about a neutral baseline."""

    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "chart--surplus",
        "chart--deficit",
        "chart--baseline",
        "chart--label",
        "chart--figure",
    }

    def __init__(self, *, height: int = 7, **kwargs: Unpack[WidgetOptions]) -> None:
        super().__init__(**kwargs)
        self.columns: tuple[Column, ...] = ()
        self.rows = max(3, height)

    def show(self, columns: list[Column]) -> None:
        self.columns = tuple(columns)
        self.refresh()

    def render(self) -> Text:
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
        """Rows each side of the baseline, split in proportion to the data.

        A series with no deficit weeks does not get rows of empty negative axis.
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
        """Distance above the baseline to the largest surplus, clamped at zero.

        A distance, not a value: `_arms` divides by `high + low`, and without
        the clamp a lone negative column makes that sum zero.
        """
        return max(0.0, max((column.value for column in shown), default=0.0))

    @staticmethod
    def _low(shown: tuple[Column, ...]) -> float:
        """Distance below the baseline to the largest deficit, clamped at zero."""
        return max(0.0, -min((column.value for column in shown), default=0.0))

    def _fit(self) -> tuple[tuple[Column, ...], int]:
        """The most recent bars that fit, and whether they get a gap.

        Trimmed from the left, so the weeks dropped are the oldest.
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
        """Draw one row of the chart, at a fixed distance from the baseline."""
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
        """Name the best and worst bars; labelling all of them is unreadable."""
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
    """A single series against a reference rule."""

    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "chart--series",
        "chart--reference",
        "chart--label",
        "chart--figure",
    }

    def __init__(self, **kwargs: Unpack[WidgetOptions]) -> None:
        super().__init__(**kwargs)
        self.remaining: float | None = None
        self.total: float = 0.0
        self.pace: float | None = None

    def show(self, remaining: float | None, total: float, pace: float | None) -> None:
        self.remaining, self.total, self.pace = remaining, total, pace
        self.refresh()

    def render(self) -> Text:
        label = self.get_component_rich_style("chart--label")
        if self.remaining is None or self.total <= 0:
            return Text("No entitlement recorded", style=label)

        width = max(12, self.content_size.width)
        spent = self.total - self.remaining
        track = [EMPTY] * width
        filled = round(spent / self.total * width)
        for index in range(min(filled, width)):
            track[index] = FULL

        marker = (
            None
            if self.pace is None
            else min(width - 1, max(0, round(self.pace / self.total * width)))
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
        # A negative remainder is signed with U+2212, like every other figure
        # on the panel; `days` alone would write it with an ASCII hyphen.
        left = (
            signed_days(self.remaining)
            if self.remaining < 0
            else fmt_days(self.remaining)
        )
        text.append(
            f"{fmt_days(spent)} taken · {left} left"
            f" · pace {fmt_days(round(self.pace or 0, 1))}",
            label,
        )
        return text


class WeekRibbon(Widget):
    """Punch strips stacked on one time axis, a week to a row."""

    COMPONENT_CLASSES: ClassVar[set[str]] = {*PUNCH_CLASSES, "chart--label"}

    def __init__(
        self,
        *,
        window: Window | None = None,
        now: datetime,
        **kwargs: Unpack[WidgetOptions],
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

    def render(self) -> Text:
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
    """A calendar grid coloured by how each day went, weekday down, week across.

    Colour carries magnitude on a diverging ramp and the glyph carries day type,
    so the two encodings never fight over a cell.
    """

    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "chart--label",
        "chart--neutral",
        *(f"chart--surplus-{step}" for step in range(1, DIVERGING_STEPS + 1)),
        *(f"chart--deficit-{step}" for step in range(1, DIVERGING_STEPS + 1)),
    }

    def __init__(self, **kwargs: Unpack[WidgetOptions]) -> None:
        super().__init__(**kwargs)
        self.ledgers: dict[date, DayLedger] = {}
        self.scale = timedelta(hours=2)
        self.first_weekday = 0
        """Which day the rows start on; `render` can run before the first `show`."""

    def show(self, ledgers: list[DayLedger], *, first_weekday: int) -> None:
        self.ledgers = {item.date: item for item in ledgers}
        self.first_weekday = first_weekday
        worst = max(
            (abs(item.balance_effect) for item in ledgers), default=timedelta(hours=2)
        )
        # A floor on the scale, so a fortnight of near-perfect days is not
        # drawn as violently as a fortnight of disasters.
        self.scale = max(worst, timedelta(hours=2))
        self.refresh()

    def render(self) -> Text:
        label = self.get_component_rich_style("chart--label")
        if not self.ledgers:
            return Text("Nothing recorded yet", style=label)

        # Grid and row labels both take the configured first day, so they line
        # up with the bars above and with the calendar on the leave screen.
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
        glyph = (
            AMENDED_HEAT
            if any(segment.amended for segment in ledger.segments)
            else HEAT
        )
        effect = ledger.balance_effect
        if effect == timedelta():
            return glyph, self.get_component_rich_style("chart--neutral")
        share = min(1.0, abs(effect) / self.scale)
        step = max(1, min(DIVERGING_STEPS, round(share * DIVERGING_STEPS)))
        arm = "surplus" if effect > timedelta() else "deficit"
        return glyph, self.get_component_rich_style(f"chart--{arm}-{step}")

    def _legend(self) -> Text:
        """Draw the ramp with both ends named, so colour is never alone.

        The corrected fill is named only on a year that contains one.
        """
        label = self.get_component_rich_style("chart--label")
        text = Text(f"{MINUS}{hm(self.scale)} ", style=label)
        for step in range(DIVERGING_STEPS, 0, -1):
            text.append(HEAT, self.get_component_rich_style(f"chart--deficit-{step}"))
        text.append(HEAT, self.get_component_rich_style("chart--neutral"))
        for step in range(1, DIVERGING_STEPS + 1):
            text.append(HEAT, self.get_component_rich_style(f"chart--surplus-{step}"))
        text.append(f" +{hm(self.scale)}", label)
        if any(
            segment.amended
            for ledger in self.ledgers.values()
            for segment in ledger.segments
        ):
            text.append(f"  {AMENDED_HEAT} corrected", label)
        return text


def running_balance(ledgers: Sequence[DayLedger]) -> tuple[float, ...]:
    """The flexi balance in hours after each day, in order.

    A day off contributes what it withdrew, so a week of leave is flat;
    `balance_effect` is the one place that rule lives.
    """
    return tuple(
        accumulate(
            ledger.balance_effect.total_seconds() / SECONDS_PER_HOUR
            for ledger in ledgers
        )
    )


def week_columns(ledgers: list[DayLedger], *, first_weekday: int) -> list[Column]:
    """Group a run of days into one bar per week, for :class:`DivergingBars`.

    ``first_weekday`` buckets and labels the bars, keeping them in step with the
    calendar drawn from the same setting.
    """
    buckets: defaultdict[date, timedelta] = defaultdict(timedelta)
    for ledger in ledgers:
        buckets[week_start(ledger.date, first_weekday=first_weekday)] += (
            ledger.balance_effect
        )
    return [
        Column(
            label=str(week.day),
            value=total.total_seconds() / SECONDS_PER_HOUR,
            readout=delta(total),
        )
        for week, total in sorted(buckets.items())
    ]
