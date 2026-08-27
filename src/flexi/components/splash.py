"""The word `flexi`, extruded into three dimensions and flexed.

The image is computed rather than drawn. The wordmark is a bitmap font seven
rows tall; every inked cell is extruded into a cuboid, the exposed faces of
those cuboids are sampled into a cloud of points carrying surface normals, and
each frame the cloud is rotated, projected through a pinhole, depth-sorted into
a z-buffer and shaded by how squarely each normal faces the light. Luminance
picks a character out of a ramp. That is the machinery a certain spinning
doughnut runs on, pointed at a logo instead of a torus.

It turns once and stops. There was a wobble after the turn -- the word wrung
about its own axis and ringing down -- and it undercut the thing: a mark that
settles and then jiggles reads as a toy rather than as a title. The turn
decelerates into stillness and stays there. The last frame is exactly the flat
bitmap at full brightness, so the spectacle resolves into something legible
rather than merely stopping.

Everything is a pure function of elapsed seconds and none of it knows a terminal
exists, so the animation is tested frame by frame with no clock, no screen and
no sleeping. The point cloud is built once and cached; a frame is then a
rotation and a projection over a few thousand points, which is what keeps
thirty of them a second affordable on the interface thread.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from functools import cache
from types import MappingProxyType
from typing import Final

__all__ = (
    "CANVAS_HEIGHT",
    "CANVAS_WIDTH",
    "DEPTH",
    "DEPTH_SAMPLES",
    "DURATION",
    "EDGE_ON",
    "FACE_SAMPLES",
    "HOLD",
    "INK",
    "LETTER_GLYPHS",
    "LIGHT",
    "RAMP",
    "ROWS",
    "SCALE",
    "SPIN",
    "STRAPLINE",
    "STRAPLINE_IN",
    "TILT",
    "TRACKING",
    "TURNS",
    "VIEWER",
    "WORD",
    "cells",
    "ease_out",
    "extent",
    "is_finished",
    "luminance",
    "pitch",
    "settled_rows",
    "should_play",
    "strapline_fade",
    "surface",
    "yaw",
)

WORD: Final = "flexi."
STRAPLINE: Final = "Manage your time, flexibly."

ROWS: Final = 7
"""Rows in the drawn wordmark. Ascenders take all seven; `e` and `x` sit in the
lower five, and the dot of the `i` rides on the top one."""

INK: Final = "#"

# Seven rows apiece. Lowercase with real ascenders, because "flexi" in capitals
# is a different word about a different kind of company.
LETTER_GLYPHS: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "f": (".###", ".#..", "###.", ".#..", ".#..", ".#..", ".#.."),
        "l": ("##.", ".#.", ".#.", ".#.", ".#.", ".#.", ".##"),
        "e": (".....", ".....", ".###.", "#...#", "#####", "#....", ".###."),
        "x": (".....", ".....", "#...#", ".#.#.", "..#..", ".#.#.", "#...#"),
        "i": (".#.", "...", "##.", ".#.", ".#.", ".#.", "###"),
        ".": ("..", "..", "..", "..", "..", "..", "##"),
    }
)

TRACKING: Final = 1
"""Blank columns between letters."""

RAMP: Final = " ·-:+*░▒▓█"
"""Luminance, dimmest first. It ends in a solid block so that a face-on, fully
lit surface resolves into exactly the flat wordmark rather than into a dither."""

DEPTH: Final = 2.6
"""How far the wordmark is extruded, in cells. Enough that the sides catch the
light while it turns, little enough that it still reads as lettering."""

CANVAS_WIDTH: Final = 62
CANVAS_HEIGHT: Final = 15
"""A constant canvas, so the block never moves or resizes on the screen."""

VIEWER: Final = 60.0
"""Distance from the eye to the middle of the word.

Far enough that the perspective on the extrusion is gentle. Closer in, the near
face of the slab is scaled enough more than the far one that a cell at the top
or bottom of the word spans two rows instead of one, and the settled wordmark
comes out with its first and last rows drawn twice."""

SCALE: Final = 60.0
"""Projection scale. Equal to VIEWER, so a face-on cell is one cell wide."""

LIGHT: Final = (-0.24, 0.33, -0.91)
"""Unit vector towards the light: mostly head on, a little above and to the
left, so the extruded sides are lit differently from the face while it turns.

Head on enough that a face-on surface reads at the top of the ramp: the settled
wordmark has to be solid blocks, not the shade below them."""

EDGE_ON: Final = -0.15
"""How squarely a face must meet the eye to be drawn at all.

Culling only what points strictly away is not enough. At rest the four sides of
every cell are exactly edge-on -- no projected area whatsoever -- but their
normals are perpendicular rather than turned away, so they were still being
painted, one column to the side of the cell they belong to. That filled in the
counters: the hole in the `e` closed up and the wordmark became a slab."""

SPIN: Final = 1.70
"""Seconds the word takes to turn in and stop."""

TURNS: Final = 1.75
"""Rotations it makes on the way in."""

TILT: Final = 0.42
"""Radians the word is pitched over at the start, easing to nothing."""

STRAPLINE_IN: Final = 0.55
"""Seconds the strapline takes to fade up, once the word is still."""

HOLD: Final = 1.10
"""Seconds the finished wordmark simply sits there.

Somebody sees this once. Snatching it away the instant it settles wastes the
only moment the application has to introduce itself."""

DURATION: Final = SPIN + STRAPLINE_IN + HOLD

FACE_SAMPLES: Final = 6
"""Samples across a face, per axis.

Taken at the middle of each sub-division rather than at its edges, so that the
samples sit wholly inside the cell and neighbouring cells tile instead of
overlapping. Sampling the edges put a mark half a cell beyond the glyph on every
side, which thickened the wordmark and closed up its counters."""

DEPTH_SAMPLES: Final = 9
"""Samples through the narrow faces of the extrusion.

The word is a thin slab, so for a good part of every turn the faces are culled
and the only thing left to draw is its edge. Two samples through the depth left
that edge as scattered speckle rather than a solid rim, which read as noise
instead of as an object."""


# -- the model ---------------------------------------------------------------


def cells() -> list[tuple[int, int]]:
    """Every inked cell of the wordmark, as (column, row) from the top left."""
    inked: list[tuple[int, int]] = []
    column = 0
    for character in WORD:
        glyph = LETTER_GLYPHS[character]
        for row, line in enumerate(glyph):
            inked.extend(
                (column + offset, row)
                for offset, mark in enumerate(line)
                if mark == INK
            )
        column += len(glyph[0]) + TRACKING
    return inked


def extent() -> tuple[int, int]:
    """Columns and rows the wordmark occupies."""
    return max(column for column, _ in cells()) + 1, ROWS


@cache
def surface() -> tuple[tuple[float, float, float, float, float, float], ...]:
    """The wordmark as a cloud of lit points: position, then surface normal.

    Each inked cell is a cuboid. Only faces with no neighbouring cell against
    them are sampled -- an interior wall between two touching cells is never
    visible, and sampling it would be most of the work for none of the picture.
    """
    inked = set(cells())
    width, height = extent()
    middle_x, middle_y = (width - 1) / 2, (height - 1) / 2
    half = DEPTH / 2

    points: list[tuple[float, float, float, float, float, float]] = []
    for column, row in inked:
        # Model space: x to the right, y up, z away from the eye.
        x0, y0 = column - middle_x, middle_y - row

        for towards in (-1.0, 1.0):
            points.extend(
                (
                    x0 + ((across + 0.5) / FACE_SAMPLES - 0.5),
                    y0 + ((down + 0.5) / FACE_SAMPLES - 0.5),
                    towards * half,
                    0.0,
                    0.0,
                    towards,
                )
                for across in range(FACE_SAMPLES)
                for down in range(FACE_SAMPLES)
            )

        sides = (
            ((-1, 0), (-0.5, 0.0), (-1.0, 0.0, 0.0)),
            ((1, 0), (0.5, 0.0), (1.0, 0.0, 0.0)),
            ((0, -1), (0.0, 0.5), (0.0, 1.0, 0.0)),
            ((0, 1), (0.0, -0.5), (0.0, -1.0, 0.0)),
        )
        for (step_x, step_y), (offset_x, offset_y), normal in sides:
            if (column + step_x, row + step_y) in inked:
                continue
            points.extend(
                (
                    x0
                    + offset_x
                    + (0.0 if step_x else (along + 0.5) / FACE_SAMPLES - 0.5),
                    y0
                    + offset_y
                    + (0.0 if step_y else (along + 0.5) / FACE_SAMPLES - 0.5),
                    (deep / (DEPTH_SAMPLES - 1) - 0.5) * DEPTH,
                    *normal,
                )
                for along in range(FACE_SAMPLES)
                for deep in range(DEPTH_SAMPLES)
            )
    return tuple(points)


# -- the motion --------------------------------------------------------------


def ease_out(progress: float) -> float:
    """Fast, then slowing to a stop. Cubic, the gentlest that still reads."""
    return 1.0 - (1.0 - progress) ** 3


def yaw(elapsed: float) -> float:
    """Rotation about the upright axis: several turns, arriving square on."""
    if elapsed >= SPIN:
        return 0.0
    return TURNS * 2.0 * math.pi * (1.0 - ease_out(max(0.0, elapsed) / SPIN))


def pitch(elapsed: float) -> float:
    """Tilt towards the eye, easing away as the word lands."""
    if elapsed >= SPIN:
        return 0.0
    return TILT * (1.0 - ease_out(max(0.0, elapsed) / SPIN))


def strapline_fade(elapsed: float) -> float:
    """How far the strapline has arrived, nought to one.

    A fade rather than a typewriter. Letters appearing one at a time is the
    oldest gesture a terminal has, and it changes the width of the line on every
    frame, which is a poor thing to do underneath something being centred.
    """
    if elapsed < SPIN:
        return 0.0
    return min(1.0, (elapsed - SPIN) / STRAPLINE_IN)


def is_finished(elapsed: float) -> bool:
    return elapsed >= DURATION


# -- rendering ---------------------------------------------------------------


def luminance(elapsed: float) -> list[list[int]]:
    """The canvas as one brightness per character cell, or -1 for nothing.

    A z-buffer keeps the nearest surface in each cell, so the word occludes
    itself correctly while it turns. Faces pointing away from the eye are
    dropped before they are projected, which is half the cloud on any frame.
    """
    turn, lean = yaw(elapsed), pitch(elapsed)

    cos_turn, sin_turn = math.cos(turn), math.sin(turn)
    light_x, light_y, light_z = LIGHT
    levels = len(RAMP) - 1

    nearest = [[0.0] * CANVAS_WIDTH for _ in range(CANVAS_HEIGHT)]
    shade = [[-1] * CANVAS_WIDTH for _ in range(CANVAS_HEIGHT)]
    centre_x, centre_y = CANVAS_WIDTH // 2, CANVAS_HEIGHT // 2

    for x, y, z, nx, ny, nz in surface():
        # The pitch first, about the horizontal axis.
        cos_bend, sin_bend = math.cos(lean), math.sin(lean)
        y1, z1 = y * cos_bend - z * sin_bend, y * sin_bend + z * cos_bend
        ny1, nz1 = ny * cos_bend - nz * sin_bend, ny * sin_bend + nz * cos_bend

        # Then the turn, about the upright axis.
        x2, z2 = x * cos_turn + z1 * sin_turn, -x * sin_turn + z1 * cos_turn
        nx2, nz2 = nx * cos_turn + nz1 * sin_turn, -nx * sin_turn + nz1 * cos_turn

        if nz2 > EDGE_ON:
            continue

        lit = nx2 * light_x + ny1 * light_y + nz2 * light_z
        if lit <= 0.0:
            continue

        over = 1.0 / (z2 + VIEWER)
        # Doubled across, because a terminal cell is about twice as tall as wide.
        # Two columns per cell across, one row down: a terminal cell is about
        # twice as tall as it is wide. The half added to the row is what makes a
        # cell land wholly inside one row instead of straddling the boundary
        # between two and drawing every glyph row twice at a different weight.
        column = math.floor(centre_x + SCALE * over * x2 * 2.0)
        row = math.floor(centre_y - SCALE * over * y1 + 0.5)
        if not (0 <= column < CANVAS_WIDTH and 0 <= row < CANVAS_HEIGHT):
            continue
        if over <= nearest[row][column]:
            continue

        nearest[row][column] = over
        shade[row][column] = min(levels, int(lit * levels) + 1)
    return shade


@cache
def settled_rows() -> tuple[int, int]:
    """First and last canvas row the wordmark occupies once it has stopped.

    The canvas is tall enough for the word to tumble in, so the settled word
    sits in the middle of it with several blank rows either side. Anything meant
    to read as part of the logo has to be placed against these rather than
    against the canvas, or it ends up stranded a hand's width below the word.
    """
    canvas = luminance(DURATION)
    inked = [at for at, row in enumerate(canvas) if any(level >= 0 for level in row)]
    return inked[0], inked[-1]


def should_play(*, interactive: bool, animations: bool) -> bool:
    """Whether to run it at all.

    Textual does not detect a missing terminal, and ``animation_level`` gates
    the Animator but not a timer -- so a per-frame splash keeps running in CI,
    in a pipe and over a dumb terminal unless something asks first. This is that
    something.
    """
    return interactive and animations
