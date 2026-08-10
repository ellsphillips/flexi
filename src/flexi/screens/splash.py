"""The splash `flexi init` opens with, and the rules for not opening it."""

from __future__ import annotations

import sys
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
        """One Text, built row by row, so the block centres as a block.

        Every row is padded to the same width before it is centred. Centring
        each row on its own content would let the wordmark shuffle sideways as
        the tracking changes, which is the one movement it must not make.
        """
        grid = splash.frame(self._elapsed)
        cells = max((len(row) for row in grid), default=0)

        room = self.size.width or self.app.size.width or FALLBACK_WIDTH
        wide = cells * 2 <= room
        lines = splash.render(
            grid, cell="██" if wide else "█", blank="  " if wide else " "
        )

        width = max(
            max((len(line) for line in lines), default=0), len(splash.STRAPLINE)
        )
        top, bottom = colour("c-accent"), colour("c-accent-lift")

        # The ramp runs across the drawn rows rather than across the block. The
        # block is a constant height and the word inside it is not, so spreading
        # the gradient over the padding as well left the wordmark using only the
        # middle of the ramp -- and using a different part of it at every height.
        inked = [at for at, line in enumerate(lines) if line.strip()]
        first, final = (inked[0], inked[-1]) if inked else (0, 1)
        depth = max(final - first, 1)

        art = Text(no_wrap=True)
        for index, line in enumerate(lines):
            down = min(1.0, max(0.0, (index - first) / depth))
            art.append(line.center(width), style=f"bold {_blend(top, bottom, down)}")
            art.append("\n")
        art.append("\n")
        arrived = splash.strapline_fade(self._elapsed)
        art.append(
            splash.STRAPLINE.center(width),
            style=_blend(BACKGROUND, colour("c-muted"), arrived),
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
