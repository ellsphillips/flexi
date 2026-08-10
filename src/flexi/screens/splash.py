"""The splash `flexi init` opens with, and the rules for not opening it."""

from __future__ import annotations

import sys
from functools import cache
from typing import ClassVar, Final

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Static

from flexi.components import splash
from flexi.theme import colour

FRAME_SECONDS = 1 / 30
"""Thirty frames a second. Sixty buys nothing over a terminal and costs a lot
over SSH."""

BACKGROUND: Final = "#0F0E0D"
"""The screen behind the animation. Named here as well as in the stylesheet
because the strapline fades up out of it, and a fade needs both ends."""

FALLBACK_WIDTH: Final = 80
"""Assumed terminal width for the first frame, drawn from `on_mount` before
Textual has laid anything out and while `size` is still zero."""


@cache
def _shade(level: int) -> str:
    """The colour of one step of the luminance ramp.

    Out of the background, through the accent, up to its lift -- so that the
    lighting and the colour say the same thing about the same surface.
    """
    half = (len(splash.RAMP) - 1) / 2
    if level <= half:
        return _blend(BACKGROUND, colour("c-accent"), level / half)
    return _blend(colour("c-accent"), colour("c-accent-lift"), (level - half) / half)


def _blend(start: str, end: str, amount: float) -> str:
    """A colour part way between two, for the gradient down the wordmark."""
    first = tuple(int(start[at : at + 2], 16) for at in (1, 3, 5))
    second = tuple(int(end[at : at + 2], 16) for at in (1, 3, 5))
    mixed = (
        round(one + (two - one) * amount)
        for one, two in zip(first, second, strict=True)
    )
    return "#{:02X}{:02X}{:02X}".format(*mixed)


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
    #
    # `width: auto` matters: a fixed width leaves the block sitting against the
    # left edge of a container that is itself centred, which puts the wordmark
    # visibly off-centre while looking, in the stylesheet, as though it is not.
    # The canvas is a constant size, so centring it is the whole of the layout.
    DEFAULT_CSS = """
    SplashScreen { align: center middle; background: #0F0E0D; }
    #splash-body { width: auto; height: auto; }
    #splash-art { width: auto; height: auto; }
    """

    def __init__(self) -> None:
        super().__init__()
        self._elapsed = 0.0
        self._timer: Timer | None = None
        self._finished = False

    def compose(self) -> ComposeResult:
        with Container(id="splash-body"):
            yield Static(id="splash-art")

    def on_mount(self) -> None:
        self._draw()
        self._timer = self.set_interval(FRAME_SECONDS, self._tick)

    def _tick(self) -> None:
        self._elapsed += FRAME_SECONDS
        self._draw()
        if splash.is_finished(self._elapsed):
            self.action_skip()

    def _draw(self) -> None:
        """The canvas, coloured by how lit each character is.

        Colour follows luminance rather than position. The shading already
        carries the form, so a gradient by row would be a second, unrelated
        story told over the top of it: the dim rim of the slab sits just out of
        the background and the fully lit face reaches the accent's brightest
        step, which is what makes the settled wordmark read as solid.

        Runs of equal brightness are appended together. A style per character
        would be nine hundred spans a frame, thirty times a second.
        """
        levels = splash.luminance(self._elapsed)
        width = max(splash.CANVAS_WIDTH, len(splash.STRAPLINE))
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
        self.query_one("#splash-art", Static).update(art)

    def action_skip(self) -> None:
        """Dismiss once, dismiss only itself, and stop the clock behind it.

        ``dismiss`` pops the top of the stack rather than the screen it is
        called on. With the splash pushed underneath the setup form, reaching
        the end of the animation therefore deleted the *form* -- leaving its
        own last frame on screen with nothing behind it and no way to finish
        setting up. ``is_running`` did not catch that: it means mounted, not
        frontmost.

        Dismissal is also not synchronous, so without the latch the next frame
        thirty-three milliseconds later resolves the same future a second time,
        which is an ``InvalidStateError``.

        Not being frontmost is left retryable rather than latched: the next
        frame simply tries again, so a splash that is briefly covered still
        lifts once it is back on top.
        """
        if self._finished or self.app.screen is not self:
            return
        self._finished = True
        if self._timer is not None:
            self._timer.stop()
        self.dismiss(None)


def wanted(*, animation_level: str) -> bool:
    """Whether this terminal should see it."""
    return splash.should_play(
        interactive=sys.stdout.isatty(),
        animations=animation_level != "none",
    )
