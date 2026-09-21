"""The animation, checked frame by frame without running a clock.

Every frame is a pure function of elapsed seconds, so the geometry, the motion
and the shading are all testable directly. `time_machine` does not freeze the
clock Textual animates against.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass

import pytest
from textual.app import App, ComposeResult

from flexi.components import splash, wordmark
from flexi.components.wordmark import FRAME_SECONDS, Wordmark

BRIGHTEST = len(splash.RAMP) - 1

NARROW_CANVAS = 10
"""Far narrower than the word, so most of it projects off the edge."""


def where(column: int, row: int) -> tuple[int, int]:
    """Where the centre of a model cell lands once the word has settled."""
    width, height = splash.extent()
    x = column - (width - 1) / 2
    y = (height - 1) / 2 - row
    over = 1.0 / (splash.VIEWER - splash.DEPTH / 2)
    across = math.floor(splash.CANVAS_WIDTH // 2 + splash.SCALE * over * x * 2.0)
    down = math.floor(splash.CANVAS_HEIGHT // 2 - splash.SCALE * over * y + 0.5)
    return across, down


def lit(canvas: list[list[int]]) -> int:
    return sum(1 for row in canvas for level in row if level >= 0)


@dataclass
class Pretend:
    """A stdout stand-in: pytest's own is never a terminal."""

    tty: bool

    def isatty(self) -> bool:
        return self.tty


class Turning(App[None]):
    """The wordmark on its own, counting how often it says it has landed."""

    def __init__(self) -> None:
        super().__init__()
        self.landings = 0

    def compose(self) -> ComposeResult:
        yield Wordmark()

    def on_wordmark_landed(self, _message: Wordmark.Landed) -> None:
        self.landings += 1


# ---- the model ----


def test_every_letter_is_the_same_height() -> None:
    for character, rows in splash.LETTER_GLYPHS.items():
        assert len(rows) == splash.ROWS, character


def test_every_letter_is_rectangular() -> None:
    for character, rows in splash.LETTER_GLYPHS.items():
        assert len({len(row) for row in rows}) == 1, character


def test_word_uses_only_defined_letters() -> None:
    assert set(splash.WORD) <= set(splash.LETTER_GLYPHS)
    assert splash.WORD.startswith("flexi")


def test_wordmark_has_ink_in_every_row() -> None:
    rows = {row for _, row in splash.cells()}
    assert rows == set(range(splash.ROWS))


def test_interior_walls_are_never_sampled() -> None:
    """Faces between two touching cells cannot be seen from anywhere."""
    inked = splash.cells()
    every_face = len(inked) * (
        2 * splash.FACE_SAMPLES**2 + 4 * splash.FACE_SAMPLES * splash.DEPTH_SAMPLES
    )
    assert len(splash.surface()) < every_face


def test_cloud_stays_affordable() -> None:
    """The cloud is rotated and projected on the interface thread every frame."""
    assert len(splash.surface()) < 20_000


def test_cloud_is_built_once() -> None:
    assert splash.surface() is splash.surface()


def test_every_normal_is_a_unit_vector() -> None:
    for _, _, _, nx, ny, nz in splash.surface():
        assert math.isclose(math.sqrt(nx * nx + ny * ny + nz * nz), 1.0)


# ---- the motion ----


def test_word_turns_several_times_before_landing() -> None:
    assert splash.yaw(0.0) == pytest.approx(splash.TURNS * 2 * math.pi)
    assert splash.yaw(splash.SPIN) == 0.0


def test_spin_slows_into_the_landing() -> None:
    """Cubic ease-out: most of the turning is done early."""
    half = splash.yaw(splash.SPIN / 2)
    assert half < splash.yaw(0.0) / 4


def test_word_lands_square_on_and_stays_there() -> None:
    for at in (splash.SPIN, splash.SPIN + 0.4, splash.DURATION):
        assert splash.yaw(at) == 0.0
        assert splash.pitch(at) == 0.0


def test_nothing_moves_once_it_has_landed() -> None:
    """Every frame from the landing to the end is the same picture."""
    landed = splash.luminance(splash.SPIN)
    for at in (splash.SPIN + 0.05, splash.SPIN + 0.9, splash.DURATION):
        assert splash.luminance(at) == landed, f"it moved again at {at:.2f}s"


@pytest.mark.parametrize("at", [0.0, 0.3, 0.7, 1.1, 1.6, 2.2, 2.8, 3.35])
def test_canvas_never_moves_or_changes_size(at: float) -> None:
    canvas = splash.luminance(at)
    assert len(canvas) == splash.CANVAS_HEIGHT
    assert {len(row) for row in canvas} == {splash.CANVAS_WIDTH}


@pytest.mark.parametrize("at", [0.0, 0.3, 0.7, 1.1, 1.6, 2.2, 2.8, 3.35])
def test_there_is_always_something_on_screen(at: float) -> None:
    """Including edge on, where a slab with no depth would vanish."""
    assert lit(splash.luminance(at)) > 40


def test_settled_frame_is_the_flat_wordmark() -> None:
    canvas = splash.luminance(splash.DURATION)
    for column, row in splash.cells():
        across, down = where(column, row)
        assert canvas[down][across] == BRIGHTEST, f"cell {column},{row} is not solid"


def test_settled_frame_is_solid() -> None:
    canvas = splash.luminance(splash.DURATION)
    shades = {level for row in canvas for level in row if level >= 0}
    assert shades == {BRIGHTEST}


def test_settled_wordmark_is_not_a_solid_slab() -> None:
    """The counters stay open: a cell's side faces are edge-on at rest.

    Culling only what points backwards would paint them beside their own cell.
    """
    canvas = splash.luminance(splash.DURATION)
    rows = [at for at, row in enumerate(canvas) if any(level >= 0 for level in row)]
    columns = [
        at for at in range(splash.CANVAS_WIDTH) if any(row[at] >= 0 for row in canvas)
    ]
    box = (rows[-1] - rows[0] + 1) * (columns[-1] - columns[0] + 1)
    assert lit(canvas) < box * 0.6, "the wordmark is filled in"


def test_settled_wordmark_is_the_font_height() -> None:
    canvas = splash.luminance(splash.DURATION)
    rows = [at for at, row in enumerate(canvas) if any(level >= 0 for level in row)]
    assert rows[-1] - rows[0] + 1 == splash.ROWS


def test_back_faces_are_not_drawn() -> None:
    """A back face would show through the front of the word."""
    canvas = splash.luminance(0.0)
    assert lit(canvas) < len(splash.surface())


def test_word_wider_than_the_canvas_is_cropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A negative column is a valid index, and it addresses the far edge.

    Uncropped, a cell projecting off the left is painted on the right and takes
    the depth buffer with it. The canvas is wide enough for the word as it
    stands, so a longer word fails as a smear and not as an exception.
    """
    full = splash.luminance(splash.DURATION)
    margin = splash.CANVAS_WIDTH // 2 - NARROW_CANVAS // 2

    monkeypatch.setattr(splash, "CANVAS_WIDTH", NARROW_CANVAS)
    cropped = splash.luminance(splash.DURATION)

    assert {len(row) for row in cropped} == {NARROW_CANVAS}
    assert cropped == [row[margin : margin + NARROW_CANVAS] for row in full]


# ---- the strapline ----


def test_strapline_waits_for_the_word_to_stop() -> None:
    assert splash.strapline_fade(0.0) == 0.0
    assert splash.strapline_fade(splash.SPIN - 0.01) == 0.0


def test_strapline_arrives_and_finishes() -> None:
    begun = splash.SPIN + splash.STRAPLINE_IN / 2
    assert 0.0 < splash.strapline_fade(begun) < 1.0
    assert splash.strapline_fade(splash.DURATION) == 1.0
    assert splash.STRAPLINE == "Manage your time, flexibly."


# ---- timing ----


def test_splash_holds_still_once_it_arrives() -> None:
    """A second of stillness follows the strapline, so the end is not a snatch."""
    settled = splash.SPIN + splash.STRAPLINE_IN
    assert splash.DURATION - settled >= 1.0
    assert not splash.is_finished(settled + 0.5)


def test_splash_ends() -> None:
    assert not splash.is_finished(splash.DURATION - 0.01)
    assert splash.is_finished(splash.DURATION)


def test_splash_lasts_three_to_six_seconds() -> None:
    assert 3.0 <= splash.DURATION <= 6.0


@pytest.mark.parametrize(
    ("interactive", "animations", "expected"),
    [(True, True, True), (False, True, False), (True, False, False)],
)
def test_splash_plays_only_on_an_animating_terminal(
    *, interactive: bool, animations: bool, expected: bool
) -> None:
    """Textual's `animation_level` gates the Animator, not a timer.

    A per-frame splash on a timer keeps running in CI and in a pipe.
    """
    assert (
        splash.should_play(interactive=interactive, animations=animations) is expected
    )


# ---- the widget ----


def test_widget_turns_only_on_an_animating_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The widget joins the two halves: a real terminal, and animation left on."""
    monkeypatch.setattr(sys, "stdout", Pretend(tty=False))
    assert wordmark.wanted(animation_level="full") is False

    monkeypatch.setattr(sys, "stdout", Pretend(tty=True))
    assert wordmark.wanted(animation_level="full") is True
    assert wordmark.wanted(animation_level="none") is False


async def test_landing_is_announced_once() -> None:
    """The message reveals the setup questions, and `skip` is a second way in."""
    app = Turning()
    async with app.run_test(size=(60, 20)) as pilot:
        mark = app.query_one(Wordmark)

        mark._elapsed = splash.DURATION - FRAME_SECONDS / 2
        mark._tick()
        await pilot.pause()
        assert app.landings == 1

        mark.skip()
        await pilot.pause()

        assert app.landings == 1, "it landed twice"
