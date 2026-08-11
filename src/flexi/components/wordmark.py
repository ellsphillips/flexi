"""The animated wordmark, as a widget that something else can sit under.

It was a screen of its own, pushed over the setup form and dismissed when the
animation finished. That is one screen too many. Dismissing pops the top of the
stack rather than the screen doing the dismissing, so a splash pushed at the
wrong moment deleted the form underneath it -- and the only thing the splash
ever preceded was that form. Making it a widget removes the second screen, and
with it a whole class of mistake: there is nothing left to pop.

It also buys the arrangement the animation wanted all along. The word turns in,
lands, and stays exactly where it is while the questions arrive underneath it.
"""

from __future__ import annotations

import sys
from typing import Any, Final

from rich.text import Text
from textual.message import Message
from textual.timer import Timer
from textual.widgets import Static

from flexi.components import splash
from flexi.theme import colour

FRAME_SECONDS: Final = 1 / 30
"""Thirty frames a second. Sixty buys nothing over a terminal and costs a lot
over SSH."""

BACKGROUND: Final = "#0F0E0D"
"""The ground the strapline fades up out of. A fade needs both ends."""


def _blend(start: str, end: str, amount: float) -> str:
    """A colour part of the way between two others."""
    first = tuple(int(start[at : at + 2], 16) for at in (1, 3, 5))
    second = tuple(int(end[at : at + 2], 16) for at in (1, 3, 5))
    mixed = (
        round(one + (two - one) * amount)
        for one, two in zip(first, second, strict=True)
    )
    return "#{:02X}{:02X}{:02X}".format(*mixed)


def _shade(level: int) -> str:
    """The colour of one step of the luminance ramp.

    Out of the background, through the accent, up to its lift -- so the lighting
    and the colour say the same thing about the same surface.
    """
    half = (len(splash.RAMP) - 1) / 2
    if level <= half:
        return _blend(BACKGROUND, colour("c-accent"), level / half)
    return _blend(colour("c-accent"), colour("c-accent-lift"), (level - half) / half)


def wanted(*, animation_level: str) -> bool:
    """Whether this terminal should see the animation."""
    return splash.should_play(
        interactive=sys.stdout.isatty(),
        animations=animation_level != "none",
    )


class Wordmark(Static):
    """`flexi`, computed in three dimensions, turning in and settling."""

    DEFAULT_CSS = """
    Wordmark { width: auto; }
    """

    STRAPLINE_ROWS: Final = 2
    """A blank row and the strapline, under the canvas."""

    class Landed(Message):
        """The word has stopped moving. Whatever waits beneath it may arrive."""

    def __init__(self, *, animate: bool = True, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._plays = animate
        self._elapsed = 0.0 if animate else splash.DURATION
        self._timer: Timer | None = None
        self._landed = False

    def on_mount(self) -> None:
        # Height from the canvas rather than measured off the content: leaving
        # it to `height: auto` inside a vertical alongside other things resolved
        # it to a single row, and the animation played, correctly, one row tall.
        # The width is left to the stylesheet, so the wordmark can be told to
        # fill whatever it is centred over.
        self.styles.height = splash.CANVAS_HEIGHT + self.STRAPLINE_ROWS
        self._draw()
        if not self._plays:
            self._land()
            return
        self._timer = self.set_interval(FRAME_SECONDS, self._tick)

    def on_resize(self) -> None:
        """Redraw at the new width, so the centring follows the widget.

        Without this a wordmark that is not animating is drawn once, before
        layout has given it a width, and stays centred on the fallback.
        """
        self._draw()

    def _tick(self) -> None:
        self._elapsed += FRAME_SECONDS
        self._draw()
        if splash.is_finished(self._elapsed):
            self._land()

    def skip(self) -> None:
        """Cut to the end. Somebody setting up twice need not watch it twice."""
        self._elapsed = splash.DURATION
        self._draw()
        self._land()

    def _land(self) -> None:
        """Announce the landing once, and stop the clock that would announce it again.

        The timer is not stopped by anything else, and a message posted on every
        subsequent frame would reveal the questions thirty times a second.
        """
        if self._landed:
            return
        self._landed = True
        if self._timer is not None:
            self._timer.stop()
        self.post_message(self.Landed())

    def _draw(self) -> None:
        """The canvas, coloured by how lit each character is.

        Colour follows luminance rather than position: the shading already
        carries the form, so a gradient by row would be a second, unrelated
        story told over the top of it.

        Runs of equal brightness are appended together. A style per character
        would be nine hundred spans a frame, thirty times a second.
        """
        levels = splash.luminance(self._elapsed)
        # Centred on the widget, not on the canvas: the widget is as wide as
        # whatever sits under it, and the logo has to be centred over that.
        width = max(splash.CANVAS_WIDTH, len(splash.STRAPLINE), self.size.width)
        margin = " " * ((width - splash.CANVAS_WIDTH) // 2)

        art = Text(no_wrap=True)
        for row in levels:
            art.append(margin)
            at = 0
            while at < len(row):
                level = row[at]
                run = at
                while run < len(row) and row[run] == level:
                    run += 1
                if level < 0:
                    art.append(" " * (run - at))
                else:
                    art.append(
                        splash.RAMP[level] * (run - at), style=f"bold {_shade(level)}"
                    )
                at = run
            art.append("\n")

        art.append("\n")
        art.append(
            splash.STRAPLINE.center(width),
            style=_blend(
                BACKGROUND, colour("c-muted"), splash.strapline_fade(self._elapsed)
            ),
        )
        self.update(art)
