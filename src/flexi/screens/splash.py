"""The splash `flexi init` opens with, and the rules for not opening it."""

from __future__ import annotations

import sys
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container
from textual.screen import Screen
from textual.widgets import Static

from flexi.components import splash

FRAME_SECONDS = 1 / 30
"""Thirty frames a second. Sixty buys nothing over a terminal and costs a lot
over SSH."""


class SplashScreen(Screen[None]):
    """`flexi`, squished and stretched, with the strapline arriving under it.

    Dismisses itself when the animation finishes, or on any key. Somebody
    setting Flexi up for the second time should not have to watch it twice.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape,space,enter,q", "skip", "Skip", show=False),
    ]

    # Literal, not $c-ink. An undefined variable in a stylesheet fails during
    # CSS parse at startup, which would take the whole application down rather
    # than skipping an animation.
    DEFAULT_CSS = """
    SplashScreen { align: center middle; background: #0F0E0D; }
    #splash-body { width: 60; height: auto; }
    """

    def __init__(self) -> None:
        super().__init__()
        self._elapsed = 0.0

    def compose(self) -> ComposeResult:
        with Container(id="splash-body"):
            yield Static(id="splash-art")

    def on_mount(self) -> None:
        self._draw()
        self.set_interval(FRAME_SECONDS, self._tick)

    def _tick(self) -> None:
        self._elapsed += FRAME_SECONDS
        self._draw()
        if splash.is_finished(self._elapsed):
            self.action_skip()

    def _draw(self) -> None:
        """One Text, built as lines, so the block centres as a block.

        The wordmark is centred as a unit -- word plus its full stop -- and the
        stop is coloured by span afterwards. Centring the two separately walks
        the dot away from the word as the tracking changes.
        """
        mark = f"{splash.word(self._elapsed)}·"
        width = max(len(mark), len(splash.STRAPLINE))

        lines = ["" for _ in range(splash.squash(self._elapsed))]
        lines.append(mark.center(width))
        lines.append("")
        lines.append(splash.strapline(self._elapsed).center(width))

        art = Text("\n".join(lines), no_wrap=True)
        start = "\n".join(lines).index(mark)
        art.stylize("bold #00AAAD", start, start + len(mark) - 1)
        art.stylize("#4CDCDF", start + len(mark) - 1, start + len(mark))
        art.stylize("#9C948A", start + len(mark))
        self.query_one("#splash-art", Static).update(art)

    def action_skip(self) -> None:
        if self.is_running:
            self.dismiss(None)


def wanted(*, animation_level: str) -> bool:
    """Whether this terminal should see it."""
    return splash.should_play(
        interactive=sys.stdout.isatty(),
        animations=animation_level != "none",
    )
