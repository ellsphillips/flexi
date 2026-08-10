"""The word `flexi`, squished and stretched like something soft.

A terminal cell grid cannot scale a glyph, so the squash has to come from
*tracking* -- the spaces between the letters -- and from the height of the block
the word sits in. Pulled wide the letters separate and the block flattens;
released, they spring back past rest and settle. That overshoot is the whole
effect: a linear return reads as a slide, and a spring reads as a plushy.

Everything here is a pure function of elapsed seconds, so the shape of the
animation can be tested without running a clock. Textual 8.2.8 can only animate
Scalars, two floats and Colors -- not padding, and not text -- so none of this
could have gone through the CSS animation system anyway.
"""

from __future__ import annotations

import math
from typing import Final

WORD: Final = "flexi"
STRAPLINE: Final = "Manage your time, flexibly."

SQUASH: Final = 0.45
"""How long the word stays compressed before it is let go, in seconds."""

SPRING: Final = 0.95
"""How long the spring takes to settle after that."""

STRAPLINE_IN: Final = 0.40
"""How long the strapline takes to arrive, once the word has settled."""

DURATION: Final = SQUASH + SPRING + STRAPLINE_IN

STRETCH: Final = 7
"""Spaces between letters at full stretch. Wide enough that the springing back
is legible frame by frame; beyond this it stops reading as one word."""

REST: Final = 1
"""Where it settles. One space is the wordmark, breathing."""

DECAY: Final = 3.2
"""How fast the wobble dies away. Lower rings for longer."""

WOBBLES: Final = 2.4
"""Oscillations across the spring, in half-turns."""


def tracking(elapsed: float) -> int:
    """Spaces between the letters at this moment.

    Held compressed, then released as a damped oscillation about the rest
    width -- so it flies wide, overshoots past rest on the way back, and rings
    down. A single ease-out would read as a slide; the ringing is the plushy.
    """
    if elapsed < SQUASH:
        # Held flat. The pause is what makes the release feel like a release.
        return 0
    progress = min(1.0, (elapsed - SQUASH) / SPRING)
    swing = (STRETCH - REST) * math.exp(-DECAY * progress)
    return max(0, round(REST + swing * math.cos(WOBBLES * math.pi * progress)))


def squash(elapsed: float) -> int:
    """Blank lines above the word: none while compressed, one once it lifts."""
    return 0 if elapsed < SQUASH else 1


def word(elapsed: float) -> str:
    """The wordmark at this moment."""
    return (" " * tracking(elapsed)).join(WORD)


def strapline(elapsed: float) -> str:
    """The strapline, arriving a letter at a time once the word has settled."""
    begins = SQUASH + SPRING
    if elapsed < begins:
        return ""
    progress = min(1.0, (elapsed - begins) / STRAPLINE_IN)
    return STRAPLINE[: round(len(STRAPLINE) * progress)]


def is_finished(elapsed: float) -> bool:
    return elapsed >= DURATION


def frame(elapsed: float) -> list[str]:
    """Every line of the splash at this moment, top to bottom."""
    return [*[""] * squash(elapsed), word(elapsed), "", strapline(elapsed)]


def should_play(*, interactive: bool, animations: bool) -> bool:
    """Whether to run it at all.

    Textual does not detect a missing terminal, and ``animation_level`` gates
    the Animator but not a timer -- so a per-frame splash keeps running in CI,
    in a pipe and over a dumb terminal unless something asks first. This is that
    something.
    """
    return interactive and animations
