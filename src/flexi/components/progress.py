"""How far through the day, and the period, you are.

Progress is worked against expected, not wall-clock against the working day: a
seven o'clock start is further through the day than a ten o'clock start.
"""

from __future__ import annotations

from datetime import timedelta
from typing import ClassVar, Final, Unpack

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget

from flexi.components.common import styled_track
from flexi.components.options import WidgetOptions
from flexi.domain.format import hm

__all__ = ("MIN_RAIL", "ProgressRail", "TimeProgress")

MIN_RAIL: Final = 8


class ProgressRail(Widget):
    """One labelled bar: a share of something done, and the figures behind it.

    Overshoot is drawn, not clipped: a bar that stopped at full would draw a
    ten-hour day against a seven-hour contract as an ordinary one.
    """

    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "rail--label",
        "rail--track",
        "rail--fill",
        "rail--over",
        "rail--figure",
    }

    def __init__(self, label: str, **kwargs: Unpack[WidgetOptions]) -> None:
        super().__init__(**kwargs)
        self.label = label
        self.done = timedelta()
        self.total = timedelta()
        self.compact = False

    def show(
        self,
        done: timedelta,
        total: timedelta,
        *,
        label: str | None = None,
        compact: bool = False,
    ) -> None:
        """Draw a reading, and relabel the rail if this one is named differently.

        `label` belongs here because it is a plain attribute: `rail.label = x`
        changes nothing until something calls `refresh()`.
        """
        self.done, self.total, self.compact = done, total, compact
        if label is not None:
            self.label = label
        self.refresh()

    @property
    def share(self) -> float:
        """How much of the expectation is met, uncapped."""
        if self.total <= timedelta():
            return 1.0 if self.done else 0.0
        return self.done / self.total

    def render(self) -> Text:
        label_style = self.get_component_rich_style("rail--label")
        figure_style = self.get_component_rich_style("rail--figure")
        readout = self._readout()

        text = Text(no_wrap=True, end="")
        text.append(f"{self.label} ", label_style)
        width = self.content_size.width - len(self.label) - len(readout) - 2
        if width >= MIN_RAIL:
            text.append(self._bar(width))
            text.append(" ")
        text.append(readout, figure_style)
        return text

    def _readout(self) -> str:
        if self.total <= timedelta():
            return hm(self.done) if self.done else "—"
        if self.compact:
            return f"{round(self.share * 100)}%"
        return f"{hm(self.done)} of {hm(self.total)}"

    def _bar(self, width: int) -> Text:
        """The track, filled to the share, with anything past full called out.

        The last cell becomes the overshoot mark: a rail longer than its own
        track would push the figures about.
        """
        share = self.share
        return styled_track(
            width,
            track=self.get_component_rich_style("rail--track"),
            fill=self.get_component_rich_style("rail--fill"),
            filled=min(width, max(0, round(min(share, 1.0) * width))),
            mark=(
                (width - 1, self.get_component_rich_style("rail--over"))
                if share > 1.0
                else None
            ),
        )


class TimeProgress(Horizontal):
    """The day rail and the period rail, docked under the header."""

    def compose(self) -> ComposeResult:
        yield ProgressRail("TODAY", id="rail-day")
        yield ProgressRail("WEEK", id="rail-period")

    def show(
        self,
        *,
        day_done: timedelta,
        day_total: timedelta,
        period_label: str,
        period_done: timedelta,
        period_total: timedelta,
        compact: bool = False,
    ) -> None:
        day = self.query_one("#rail-day", ProgressRail)
        day.show(day_done, day_total, compact=compact)

        period = self.query_one("#rail-period", ProgressRail)
        period.show(
            period_done, period_total, label=period_label.upper(), compact=compact
        )
