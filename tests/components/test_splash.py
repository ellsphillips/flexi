"""The shape of the squish, tested without running a clock.

Every frame is a pure function of elapsed seconds, so the animation can be
checked frame by frame in microseconds. A test that waited for the real
animation would add four minutes to the suite -- time_machine does not freeze
the clock Textual animates against.
"""

from __future__ import annotations

import pytest

from flexi.components import splash


def test_it_starts_compressed() -> None:
    """Held flat. The pause is what makes the release read as a release."""
    assert splash.tracking(0.0) == 0
    assert splash.word(0.0) == "flexi"


def test_it_is_still_held_just_before_release() -> None:
    assert splash.tracking(splash.SQUASH - 0.01) == 0


def test_it_flies_wide_the_moment_it_is_released() -> None:
    released = splash.tracking(splash.SQUASH + 0.02)
    assert released >= splash.STRETCH - 2
    assert "f" in splash.word(splash.SQUASH + 0.02)


def test_it_settles_at_the_wordmark() -> None:
    assert splash.tracking(splash.DURATION) == splash.REST
    assert splash.word(splash.DURATION) == "f l e x i"


def test_it_overshoots_rather_than_sliding_home() -> None:
    """A monotonic return reads as a slide. The ringing is the plushy."""
    step = splash.SPRING / 40
    widths = [
        splash.tracking(splash.SQUASH + n * step)
        for n in range(int(splash.SPRING / step))
    ]
    tightest = min(widths)
    assert tightest < splash.REST, "it must compress past rest on the way back"
    assert widths[-1] >= tightest, "and come back out again"


def test_the_word_is_never_mangled() -> None:
    step = splash.DURATION / 60
    for n in range(61):
        letters = splash.word(n * step).replace(" ", "")
        assert letters == splash.WORD


def test_the_strapline_waits_for_the_word_to_settle() -> None:
    assert splash.strapline(0.0) == ""
    assert splash.strapline(splash.SQUASH) == ""
    assert splash.strapline(splash.SQUASH + splash.SPRING - 0.01) == ""


def test_the_strapline_arrives_a_letter_at_a_time() -> None:
    begins = splash.SQUASH + splash.SPRING
    part = splash.strapline(begins + splash.STRAPLINE_IN / 2)
    assert part
    assert splash.STRAPLINE.startswith(part)
    assert len(part) < len(splash.STRAPLINE)


def test_the_strapline_finishes() -> None:
    assert splash.strapline(splash.DURATION) == splash.STRAPLINE
    assert splash.STRAPLINE == "Manage your time, flexibly."


def test_it_ends() -> None:
    assert not splash.is_finished(splash.DURATION - 0.01)
    assert splash.is_finished(splash.DURATION)


def test_the_whole_thing_is_under_two_seconds() -> None:
    """Somebody setting up for the second time should not resent it."""
    assert splash.DURATION < 2.0


def test_a_frame_is_lines_top_to_bottom() -> None:
    lines = splash.frame(splash.DURATION)
    assert lines[-1] == splash.STRAPLINE
    assert "f l e x i" in lines


@pytest.mark.parametrize(
    ("interactive", "animations", "expected"),
    [
        (True, True, True),
        (False, True, False),
        (True, False, False),
        (False, False, False),
    ],
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
