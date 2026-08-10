"""The shape of the squish, tested without running a clock.

Every frame is a pure function of elapsed seconds, so the animation is checked
frame by frame in microseconds. A test that waited for the real thing would add
minutes to the suite, and `time_machine` does not freeze the clock Textual
animates against.
"""

from __future__ import annotations

import pytest

from flexi.components import splash


def heights_across_the_spring() -> list[int]:
    steps = 60
    return [
        splash.height(splash.SQUASH + step * splash.SPRING / steps)
        for step in range(steps + 1)
    ]


# -- the font ----------------------------------------------------------------


def test_every_letter_is_the_same_height() -> None:
    """A glyph a row short would sit off the baseline for the whole animation."""
    for character, rows in splash.GLYPHS.items():
        assert len(rows) == splash.ROWS, character


def test_every_letter_is_rectangular() -> None:
    for character, rows in splash.GLYPHS.items():
        assert len({len(row) for row in rows}) == 1, character


def test_the_word_is_spelled_with_letters_that_exist() -> None:
    assert set(splash.WORD) <= set(splash.GLYPHS)
    assert splash.WORD.startswith("flexi")


# -- the spring --------------------------------------------------------------


def test_it_starts_held_flat() -> None:
    """The pause is what makes the release read as a release."""
    assert splash.extension(0.0) == -1.0
    assert splash.height(0.0) == splash.FLAT


def test_it_is_still_held_just_before_release() -> None:
    assert splash.height(splash.SQUASH - 0.01) == splash.FLAT


def test_it_springs_taller_than_the_wordmark_really_is() -> None:
    """A shape that only grows to its own size has not been stretched."""
    assert max(heights_across_the_spring()) > splash.REST


def test_it_dips_back_under_rest_before_settling() -> None:
    """A monotonic return reads as a slide. The ringing is what reads as soft."""
    heights = heights_across_the_spring()
    peak = heights.index(max(heights))
    assert min(heights[peak:]) < splash.REST


def test_it_settles_at_exactly_the_wordmark() -> None:
    """It is on screen for over a second afterwards, so `about right` is not.

    At 2.2 wobbles the cosine was cut off part way up and the word rested one
    row flatter than it is drawn, with the bottom rows of every letter folded.
    """
    assert splash.extension(splash.DURATION) == 0.0
    assert splash.height(splash.DURATION) == splash.REST
    assert splash.tracking(splash.DURATION) == splash.REST_TRACKING


def test_the_settled_wordmark_is_the_font_itself() -> None:
    gap = "." * splash.REST_TRACKING
    expected = [
        gap.join(splash.GLYPHS[character][row] for character in splash.WORD)
        for row in range(splash.ROWS)
    ]
    assert splash.bitmap(splash.DURATION) == expected


# -- volume ------------------------------------------------------------------


def test_it_spreads_sideways_when_it_is_flattened() -> None:
    """Squash and stretch conserves volume, or it reads as a resize."""
    assert splash.tracking(0.0) == splash.WIDEST_TRACKING
    assert splash.tracking(0.0) > splash.tracking(splash.DURATION)


def test_it_closes_up_when_it_is_pulled_tall() -> None:
    heights = heights_across_the_spring()
    tallest = splash.SQUASH + heights.index(max(heights)) * splash.SPRING / 60
    assert splash.tracking(tallest) < splash.REST_TRACKING + 1


# -- compression -------------------------------------------------------------


@pytest.mark.parametrize("tall", list(range(1, 12)))
def test_no_letter_ever_vanishes(tall: int) -> None:
    """Compression folds rows together rather than dropping them.

    Slicing every other row out of a lowercase alphabet left scattered marks
    that read as noise: `e` and `x` have nothing in their top two rows, so a
    flattened word sampled from the top was mostly holes.
    """
    for character, rows in splash.GLYPHS.items():
        drawn = splash._scaled(rows, tall)
        assert len(drawn) == tall, character
        assert any(splash.INK in row for row in drawn), character


def test_a_flattened_word_keeps_most_of_its_ink() -> None:
    flat = sum(row.count(splash.INK) for row in splash.bitmap(0.0))
    rest = sum(row.count(splash.INK) for row in splash.bitmap(splash.DURATION))
    assert flat > rest * 0.5, "squashing it should not delete half the word"


# -- the block ---------------------------------------------------------------


@pytest.mark.parametrize("at", [0.0, 0.4, 0.8, 1.2, 1.9, 2.6, 3.4])
def test_the_block_never_moves_or_changes_size(at: float) -> None:
    """Anything else reads as the terminal scrolling under it."""
    rows = splash.frame(at)
    assert len(rows) == splash.STRETCH
    assert len({len(row) for row in rows}) == 1


def test_it_stays_centred_on_its_own_middle() -> None:
    """A baseline-planted word rests four rows below the centre of the screen."""
    above = splash.lift(splash.DURATION)
    below = splash.STRETCH - above - splash.height(splash.DURATION)
    assert abs(above - below) <= 1


def test_it_fits_an_eighty_column_terminal_at_its_widest() -> None:
    widest = max(len(row) for at in (0.0, 0.6, 1.2) for row in splash.frame(at))
    assert widest * 2 <= 80


# -- rendering ---------------------------------------------------------------


def test_a_cell_is_two_characters_so_the_pixel_is_square() -> None:
    drawn = splash.render([".#."], cell="██", blank="  ")
    assert drawn == ["  ██  "]


def test_a_narrow_terminal_gets_half_width_cells() -> None:
    assert splash.render([".#."], cell="█", blank=" ") == [" █ "]


# -- the strapline -----------------------------------------------------------


def test_the_strapline_waits_for_the_word_to_settle() -> None:
    assert splash.strapline_fade(0.0) == 0.0
    assert splash.strapline_fade(splash.SQUASH + splash.SPRING - 0.01) == 0.0


def test_the_strapline_arrives_and_finishes() -> None:
    part = splash.strapline_fade(
        splash.SQUASH + splash.SPRING + splash.STRAPLINE_IN / 2
    )
    assert 0.0 < part < 1.0
    assert splash.strapline_fade(splash.DURATION) == 1.0
    assert splash.STRAPLINE == "Manage your time, flexibly."


# -- timing ------------------------------------------------------------------


def test_it_holds_still_once_it_has_arrived() -> None:
    """Snatching it away as the last letter lands reads as a glitch.

    Somebody sees this once, on the only occasion the application has to
    introduce itself.
    """
    settled = splash.SQUASH + splash.SPRING + splash.STRAPLINE_IN
    assert splash.DURATION - settled >= 1.0
    assert not splash.is_finished(settled + 0.5)


def test_it_ends() -> None:
    assert not splash.is_finished(splash.DURATION - 0.01)
    assert splash.is_finished(splash.DURATION)


def test_it_is_long_enough_to_watch_and_short_enough_to_forgive() -> None:
    assert 2.5 <= splash.DURATION <= 4.5


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
