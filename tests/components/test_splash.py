"""The animation, checked frame by frame without running a clock.

Every frame is a pure function of elapsed seconds, so the geometry, the motion
and the shading are all testable in microseconds. A test that waited for the
real thing would add minutes to the suite, and `time_machine` does not freeze
the clock Textual animates against.
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
    """Where the centre of a model cell lands once the word has settled.

    The same projection the renderer uses, so the test is asking "is the
    wordmark where the wordmark should be" rather than restating the answer.
    """
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
    """Something to stand in for stdout, which under pytest is never a terminal."""

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


# -- the model ---------------------------------------------------------------


def test_every_letter_is_the_same_height() -> None:
    for character, rows in splash.GLYPHS.items():
        assert len(rows) == splash.ROWS, character


def test_every_letter_is_rectangular() -> None:
    for character, rows in splash.GLYPHS.items():
        assert len({len(row) for row in rows}) == 1, character


def test_the_word_is_spelled_with_letters_that_exist() -> None:
    assert set(splash.WORD) <= set(splash.GLYPHS)
    assert splash.WORD.startswith("flexi")


def test_the_wordmark_has_ink_in_every_row() -> None:
    """A row with nothing in it would be a band of blank across the logo."""
    rows = {row for _, row in splash.cells()}
    assert rows == set(range(splash.ROWS))


def test_interior_walls_are_never_sampled() -> None:
    """Faces between two touching cells cannot be seen from anywhere.

    Sampling them would be most of the work of every frame for none of the
    picture, so the count is a fair proxy for that culling still happening.
    """
    inked = splash.cells()
    every_face = len(inked) * (
        2 * splash.FACE_SAMPLES**2 + 4 * splash.FACE_SAMPLES * splash.DEPTH_SAMPLES
    )
    assert len(splash.surface()) < every_face


def test_the_cloud_stays_affordable() -> None:
    """It is rotated and projected thirty times a second on the interface thread."""
    assert len(splash.surface()) < 20_000


def test_the_cloud_is_built_once() -> None:
    assert splash.surface() is splash.surface()


def test_every_normal_is_a_unit_vector() -> None:
    for _, _, _, nx, ny, nz in splash.surface():
        assert math.isclose(math.sqrt(nx * nx + ny * ny + nz * nz), 1.0)


# -- the motion --------------------------------------------------------------


def test_it_turns_several_times_on_the_way_in() -> None:
    assert splash.yaw(0.0) == pytest.approx(splash.TURNS * 2 * math.pi)
    assert splash.yaw(splash.SPIN) == 0.0


def test_it_slows_into_the_landing() -> None:
    """Cubic ease-out: most of the turning is done early."""
    half = splash.yaw(splash.SPIN / 2)
    assert half < splash.yaw(0.0) / 4


def test_it_lands_square_on_and_stays_there() -> None:
    for at in (splash.SPIN, splash.SPIN + 0.4, splash.DURATION):
        assert splash.yaw(at) == 0.0
        assert splash.pitch(at) == 0.0


def test_nothing_moves_once_it_has_landed() -> None:
    """It turned, and then it wobbled, and the wobble undercut the whole thing.

    A mark that settles and then jiggles reads as a toy rather than as a title,
    so the deceleration runs into stillness and stays there. Every frame from
    the landing to the end is the same picture.
    """
    landed = splash.luminance(splash.SPIN)
    for at in (splash.SPIN + 0.05, splash.SPIN + 0.9, splash.DURATION):
        assert splash.luminance(at) == landed, f"it moved again at {at:.2f}s"


@pytest.mark.parametrize("at", [0.0, 0.3, 0.7, 1.1, 1.6, 2.2, 2.8, 3.35])
def test_the_canvas_never_moves_or_changes_size(at: float) -> None:
    canvas = splash.luminance(at)
    assert len(canvas) == splash.CANVAS_HEIGHT
    assert {len(row) for row in canvas} == {splash.CANVAS_WIDTH}


@pytest.mark.parametrize("at", [0.0, 0.3, 0.7, 1.1, 1.6, 2.2, 2.8, 3.35])
def test_there_is_always_something_on_screen(at: float) -> None:
    """Including edge on, where a slab with no depth would vanish entirely."""
    assert lit(splash.luminance(at)) > 40


def test_the_settled_frame_is_the_flat_wordmark() -> None:
    """The spectacle has to resolve into something legible, not merely stop."""
    canvas = splash.luminance(splash.DURATION)
    for column, row in splash.cells():
        across, down = where(column, row)
        assert canvas[down][across] == BRIGHTEST, f"cell {column},{row} is not solid"


def test_the_settled_frame_is_solid_rather_than_dithered() -> None:
    canvas = splash.luminance(splash.DURATION)
    shades = {level for row in canvas for level in row if level >= 0}
    assert shades == {BRIGHTEST}


def test_the_settled_wordmark_is_letters_rather_than_a_slab() -> None:
    """The counters have to stay open.

    Every cell has four side faces, and at rest they are exactly edge-on: no
    projected area at all, but normals that are perpendicular rather than turned
    away. Culling only what points backwards left them being painted a column to
    the side of the cell they belong to, which closed the hole in the `e` and
    turned the whole word into a brick.
    """
    canvas = splash.luminance(splash.DURATION)
    rows = [at for at, row in enumerate(canvas) if any(level >= 0 for level in row)]
    columns = [
        at for at in range(splash.CANVAS_WIDTH) if any(row[at] >= 0 for row in canvas)
    ]
    box = (rows[-1] - rows[0] + 1) * (columns[-1] - columns[0] + 1)
    assert lit(canvas) < box * 0.6, "the wordmark is filled in"


def test_the_settled_wordmark_is_the_height_of_the_font() -> None:
    canvas = splash.luminance(splash.DURATION)
    rows = [at for at, row in enumerate(canvas) if any(level >= 0 for level in row)]
    assert rows[-1] - rows[0] + 1 == splash.ROWS


def test_nothing_is_drawn_facing_away_from_the_eye() -> None:
    """Half the cloud, every frame, and it would show through the front."""
    canvas = splash.luminance(0.0)
    assert lit(canvas) < len(splash.surface())


def test_a_word_wider_than_the_canvas_is_cropped_rather_than_wrapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A negative column is a perfectly good index, and it addresses the far edge.

    So a cell projecting off the left would be painted on the right, and would
    take the depth buffer with it — the left of the word occluding the right of
    it. The canvas is generously sized for the word as it stands, which is
    exactly why this has to be checked deliberately: the day the word grows a
    letter, the failure is a smear rather than an exception.
    """
    full = splash.luminance(splash.DURATION)
    margin = splash.CANVAS_WIDTH // 2 - NARROW_CANVAS // 2

    monkeypatch.setattr(splash, "CANVAS_WIDTH", NARROW_CANVAS)
    cropped = splash.luminance(splash.DURATION)

    assert {len(row) for row in cropped} == {NARROW_CANVAS}
    assert cropped == [row[margin : margin + NARROW_CANVAS] for row in full]


def test_a_frame_is_text_the_width_of_the_canvas() -> None:
    rows = splash.frame(splash.DURATION)
    assert len(rows) == splash.CANVAS_HEIGHT
    assert {len(row) for row in rows} == {splash.CANVAS_WIDTH}
    assert set("".join(rows)) <= set(splash.RAMP) | {" "}


# -- the strapline -----------------------------------------------------------


def test_the_strapline_waits_for_the_word_to_stop() -> None:
    assert splash.strapline_fade(0.0) == 0.0
    assert splash.strapline_fade(splash.SPIN - 0.01) == 0.0


def test_the_strapline_arrives_and_finishes() -> None:
    begun = splash.SPIN + splash.STRAPLINE_IN / 2
    assert 0.0 < splash.strapline_fade(begun) < 1.0
    assert splash.strapline_fade(splash.DURATION) == 1.0
    assert splash.STRAPLINE == "Manage your time, flexibly."


# -- timing ------------------------------------------------------------------


def test_it_holds_still_once_it_has_arrived() -> None:
    """Snatching it away as it settles reads as a glitch rather than a title."""
    settled = splash.SPIN + splash.STRAPLINE_IN
    assert splash.DURATION - settled >= 1.0
    assert not splash.is_finished(settled + 0.5)


def test_it_ends() -> None:
    assert not splash.is_finished(splash.DURATION - 0.01)
    assert splash.is_finished(splash.DURATION)


def test_it_is_long_enough_to_watch_and_short_enough_to_forgive() -> None:
    assert 3.0 <= splash.DURATION <= 6.0


@pytest.mark.parametrize(
    ("interactive", "animations", "expected"),
    [(True, True, True), (False, True, False), (True, False, False)],
)
def test_it_only_plays_where_somebody_can_see_it(
    *, interactive: bool, animations: bool, expected: bool
) -> None:
    """Textual detects neither a missing terminal nor a timer it should stop.

    animation_level gates the Animator and not a timer, so a per-frame splash
    keeps running in CI and in a pipe unless something asks first.
    """
    assert (
        splash.should_play(interactive=interactive, animations=animations) is expected
    )


# -- the widget --------------------------------------------------------------


def test_the_word_only_turns_where_there_is_somebody_to_watch_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`flexi init` piped into a file must not spend three seconds animating.

    The widget is what joins the two halves of that decision: whether stdout is
    a terminal at all, and whether the person has turned animation off. Textual
    reports neither — its animation level gates the Animator rather than a
    timer, and a per-frame splash on a timer keeps running in a pipe.
    """
    monkeypatch.setattr(sys, "stdout", Pretend(tty=False))
    assert wordmark.wanted(animation_level="full") is False

    monkeypatch.setattr(sys, "stdout", Pretend(tty=True))
    assert wordmark.wanted(animation_level="full") is True
    assert wordmark.wanted(animation_level="none") is False


async def test_the_landing_is_announced_exactly_once() -> None:
    """The message is what reveals the setup questions underneath the word.

    Nothing else stops the timer, so a landing announced on every frame from
    then on would open the form thirty times a second — and `skip` exists
    precisely so somebody setting up twice can land it early, which is a second
    way into the same announcement.
    """
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
