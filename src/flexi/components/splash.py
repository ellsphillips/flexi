"""The word `flexi`, squished and stretched like something soft.

A terminal cannot scale a glyph, so the first version animated the *spaces*
between five ordinary letters. It read as five ordinary letters moving apart,
because that is what it was. The wordmark is drawn here instead: a bitmap seven
rows tall, in the same block characters the punch strip fills a working day
with, so the thing being squashed is an actual shape with actual mass.

That buys real squash and stretch, which is the whole of the effect. A plushy
pressed flat spreads sideways and a plushy released springs tall and narrow --
volume is conserved -- so height and tracking are driven from one damped
oscillation about rest, in opposite directions. Compressing folds rows together
rather than dropping them, because squashing something soft compacts it instead
of deleting parts of it.

Everything is a pure function of elapsed seconds, so the shape of the animation
is tested frame by frame in microseconds without a clock, a terminal or a sleep.
Rendering is left to the caller: this module deals in a grid of on and off, and
knows nothing about how wide a cell is.
"""

from __future__ import annotations

import math
from typing import Final

WORD: Final = "flexi."
STRAPLINE: Final = "Manage your time, flexibly."

ROWS: Final = 7
"""Rows in the drawn wordmark. Ascenders take all seven; `e` and `x` sit in the
lower five, and the dot of the `i` rides on the top one."""

# Seven rows apiece, ink as `#`. Lowercase with real ascenders, because "flexi"
# in capitals is a different word about a different kind of company.
GLYPHS: Final[dict[str, tuple[str, ...]]] = {
    "f": (".###", ".#..", "###.", ".#..", ".#..", ".#..", ".#.."),
    "l": ("##.", ".#.", ".#.", ".#.", ".#.", ".#.", ".##"),
    "e": (".....", ".....", ".###.", "#...#", "#####", "#....", ".###."),
    "x": (".....", ".....", "#...#", ".#.#.", "..#..", ".#.#.", "#...#"),
    "i": (".#.", "...", "##.", ".#.", ".#.", ".#.", "###"),
    ".": ("..", "..", "..", "..", "..", "..", "##"),
}

INK: Final = "#"

SQUASH: Final = 0.55
"""Seconds held flat before it is let go. The pause is what makes the release
read as a release rather than as a start."""

SPRING: Final = 1.20
"""Seconds the spring takes to ring down to rest."""

STRAPLINE_IN: Final = 0.45
"""Seconds the strapline takes to arrive, once the word has settled."""

HOLD: Final = 1.20
"""Seconds the finished wordmark simply sits there.

Somebody sees this once. Snatching it away the instant the last letter lands
wastes the only moment the application has to introduce itself, and reads as a
glitch rather than as a title."""

DURATION: Final = SQUASH + SPRING + STRAPLINE_IN + HOLD

REST: Final = ROWS
"""Height it settles at."""

FLAT: Final = 3
"""Height while held compressed."""

STRETCH: Final = 11
"""Height at the top of the spring, taller than the wordmark really is. Rows are
repeated to get there, which is what makes it read as stretched."""

REST_TRACKING: Final = 1
"""Columns between letters at rest. One is the wordmark, breathing."""

WIDEST_TRACKING: Final = 2
"""Columns between letters when flattest. Wider than this and it stops reading
as one word, and stops fitting an eighty-column terminal."""

DECAY: Final = 1.8
"""How fast the wobble dies away. Lower rings for longer."""

WOBBLES: Final = 2.5
"""Oscillations across the spring, in half-turns.

Two and a half puts a zero of the cosine exactly at the end of the spring, so
the wobble arrives at rest instead of being cut off part way up. At 2.2 it
finished an eighth of a row short and the wordmark settled one row flatter than
it is drawn, with the bottom two rows of every letter still folded together."""


def extension(elapsed: float) -> float:
    """How far from rest the spring is: -1 fully compressed, +1 fully stretched.

    Held at -1, then released as a damped cosine that passes through rest,
    overshoots, and rings down to zero. A single ease would read as a slide; the
    ringing is what reads as soft.
    """
    if elapsed < SQUASH:
        return -1.0
    progress = (elapsed - SQUASH) / SPRING
    if progress >= 1.0:
        # Exactly rest, rather than however much wobble was left. The wordmark
        # is on screen for over a second afterwards, and a resting shape that
        # is a row short of the one in the font is the sort of thing nobody
        # can name but everybody can see.
        return 0.0
    swing = math.exp(-DECAY * progress)
    return -swing * math.cos(WOBBLES * math.pi * progress)


def height(elapsed: float) -> int:
    """Rows the wordmark occupies at this moment."""
    reach = extension(elapsed)
    span = (REST - FLAT) if reach < 0 else (STRETCH - REST)
    return max(1, round(REST + reach * span))


def tracking(elapsed: float) -> int:
    """Columns between letters -- wide when flat, closed up when stretched."""
    reach = extension(elapsed)
    spread = REST_TRACKING - reach * (WIDEST_TRACKING - REST_TRACKING)
    return max(0, min(WIDEST_TRACKING, round(spread)))


def lift(elapsed: float) -> int:
    """Blank rows above, so the block is a constant height on the screen.

    The word squashes and swells about its own middle rather than settling onto
    a floor. A baseline-planted version leaves the resting wordmark sitting four
    rows below the centre of the terminal, which is the one thing a splash may
    not do; growing from the middle keeps it centred at every frame.

    Without any padding the whole word jumps up and down the screen as it
    springs, which reads as the terminal scrolling rather than as weight.
    """
    return (STRETCH - height(elapsed) + 1) // 2


def _merge(over: list[str]) -> str:
    """Several rows folded into one, keeping every mark."""
    return "".join(
        INK if any(row[at] == INK for row in over) else "."
        for at in range(len(over[0]))
    )


def _scaled(rows: tuple[str, ...], to: int) -> list[str]:
    """One glyph at a given height.

    Compressing folds rows together rather than dropping them. Squashing
    something soft compacts it; it does not delete parts of it, and slicing
    every other row out of a lowercase alphabet leaves scattered marks that
    read as noise rather than as a flattened word.

    Stretching repeats rows, which is the only way a cell grid can grow.
    """
    if to <= 0:
        return []
    if to == len(rows):
        return list(rows)
    if to > len(rows):
        return [rows[min(len(rows) - 1, i * len(rows) // to)] for i in range(to)]
    folded = []
    for band in range(to):
        first = band * len(rows) // to
        last = max(first + 1, (band + 1) * len(rows) // to)
        folded.append(_merge(list(rows[first:last])))
    return folded


def bitmap(elapsed: float) -> list[str]:
    """The wordmark at this moment, as rows of ink and space."""
    tall = height(elapsed)
    gap = "." * tracking(elapsed)
    letters = [_scaled(GLYPHS[character], tall) for character in WORD]
    return [gap.join(letter[row] for letter in letters) for row in range(tall)]


def render(rows: list[str], *, cell: str = "██", blank: str = "  ") -> list[str]:
    """A grid turned into text, one cell at whatever width the caller wants.

    Two characters per cell squares up the pixel, because a terminal cell is
    about twice as tall as it is wide. One character is the fallback for a
    terminal too narrow to hold the wordmark at full size.
    """
    return [row.replace(INK, cell).replace(".", blank) for row in rows]


def strapline_fade(elapsed: float) -> float:
    """How far the strapline has arrived, nought to one.

    A fade rather than a typewriter. Letters appearing one at a time is the
    oldest gesture a terminal has, and next to a wordmark this size it reads as
    a different piece of software; it also changes the width of the line on
    every frame, which is a poor thing to do underneath something being centred.
    """
    begins = SQUASH + SPRING
    if elapsed < begins:
        return 0.0
    return min(1.0, (elapsed - begins) / STRAPLINE_IN)


def is_finished(elapsed: float) -> bool:
    return elapsed >= DURATION


def frame(elapsed: float) -> list[str]:
    """Every row of the splash at this moment, top to bottom, as a grid.

    Padded to a constant height so the block never moves on the screen, and to
    a constant width so the centring cannot shift under it either.
    """
    drawn = bitmap(elapsed)
    width = max((len(row) for row in drawn), default=0)
    blank = "." * width
    rows = [blank for _ in range(lift(elapsed))]
    rows.extend(row.ljust(width, ".") for row in drawn)
    rows.extend(blank for _ in range(STRETCH - len(rows)))
    return rows


def should_play(*, interactive: bool, animations: bool) -> bool:
    """Whether to run it at all.

    Textual does not detect a missing terminal, and ``animation_level`` gates
    the Animator but not a timer -- so a per-frame splash keeps running in CI,
    in a pipe and over a dumb terminal unless something asks first. This is that
    something.
    """
    return interactive and animations
