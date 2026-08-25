"""How far through the day, and the period, you are.

Progress is worked against expected rather than wall-clock against the working
day: someone who started at seven is further through than someone who started at
ten, and the clock on the wall does not know that.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, ClassVar, Final

from rich.text import Text
from textual.app import ComposeResult, RenderResult
from textual.containers import Horizontal
from textual.widget import Widget

from flexi.components.common import MARKER, TRACK
from flexi.domain.format import hm

MIN_RAIL: Final = 8


class ProgressRail(Widget):
    """One labelled bar: a share of something done, and the figures behind it.

    Overshoot is drawn, not clipped. A ten-hour day against a seven-hour contract
    is the single most interesting thing a flexitime tracker can tell you, and a
    bar that stopped at full would say it was an ordinary day.
    """

    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "rail--label",
        "rail--track",
        "rail--fill",
        "rail--over",
        "rail--figure",
    }

    def __init__(self, label: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.label = label
        self.done = timedelta()
        self.total = timedelta()
        self.compact = False

    def show(self, done: timedelta, total: timedelta, *, compact: bool = False) -> None:
        self.done, self.total, self.compact = done, total, compact
        self.refresh()

    @property
    def share(self) -> float:
        """How much of the expectation is met, uncapped."""
        if self.total <= timedelta():
            return 1.0 if self.done else 0.0
        return self.done / self.total

    def render(self) -> RenderResult:
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
        """The track, filled to the share, with anything past full called out."""
        share = self.share
        filled = min(width, max(0, round(min(share, 1.0) * width)))
        glyphs = [TRACK] * width
        if share > 1.0:
            # The last cell becomes the overshoot mark rather than a longer bar:
            # a rail that grew past its own track would push the figures about.
            glyphs[-1] = MARKER

        # Glyphs first, then spans. Rebuilding a Text to swap a character drops
        # the base style, which leaves the unfilled track in the default
        # foreground — a bright line straight across the panel.
        bar = Text("".join(glyphs))
        bar.stylize(self.get_component_rich_style("rail--track"), 0, width)
        if filled:
            bar.stylize(self.get_component_rich_style("rail--fill"), 0, filled)
        if share > 1.0:
            bar.stylize(self.get_component_rich_style("rail--over"), width - 1, width)
        return bar


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
        period.label = period_label.upper()
        period.show(period_done, period_total, compact=compact)
