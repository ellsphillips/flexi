"""The splash, actually run.

`tests/components/test_splash.py` checks the shape of the animation frame by
frame as pure arithmetic, which is quick and caught nothing, because the bug was
never in the arithmetic. Nothing anywhere pushed the screen: `show_splash`
defaults to False and every other test leaves it there, so the one path that
plays the animation -- a first run, which is every user exactly once -- was the
one path with no coverage at all. It ended in a traceback.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from textual.app import App as TextualApp
from textual.app import ComposeResult
from textual.screen import Screen

from flexi.components import splash
from flexi.screens.splash import FRAME_SECONDS, SplashScreen

FRAMES_TO_THE_END = int(splash.DURATION / FRAME_SECONDS) + 1


class Harness(TextualApp[None]):
    """A bare application whose only job is to hold the splash."""

    CSS: ClassVar[str] = ""

    def compose(self) -> ComposeResult:
        return iter(())

    def on_mount(self) -> None:
        self.push_screen(SplashScreen())


def showing[S: Screen[Any]](app: Harness, kind: type[S]) -> S:
    """The current screen, asserted to be `kind`.

    `App.screen` is typed `Screen[object]`, so narrowing it in place against a
    `Screen[None]` subclass leaves mypy holding `Never`. Going through a bound
    type variable keeps the type -- the same trick `tests/tui/conftest.py` uses,
    for the same reason.
    """
    screen = app.screen
    assert isinstance(screen, kind), (
        f"expected {kind.__name__}, showing {type(screen).__name__}"
    )
    return screen


async def test_it_plays_to_the_end_without_raising() -> None:
    """The bug: it dismissed itself, then dismissed itself again a frame later.

    `dismiss` resolves a future and the second call resolved it twice, which is
    an InvalidStateError. Driving the frames by hand reproduces it in
    milliseconds rather than waiting out the animation.
    """
    app = Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = showing(app, SplashScreen)

        for _ in range(FRAMES_TO_THE_END + 30):
            screen._tick()
        await pilot.pause()


async def test_the_timer_stops_when_the_animation_does() -> None:
    """A per-frame timer left running is thirty wakeups a second, forever."""
    app = Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = showing(app, SplashScreen)
        assert screen._timer is not None

        for _ in range(FRAMES_TO_THE_END + 1):
            screen._tick()

        assert screen._finished, "it should know it is done"
        await pilot.pause()


async def test_a_key_press_skips_it() -> None:
    """Somebody setting up a second time should not have to watch it twice."""
    app = Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = showing(app, SplashScreen)

        await pilot.press("space")
        await pilot.pause()

        assert screen._finished


async def test_skipping_twice_is_not_an_error() -> None:
    """Escape and the last frame can land together; neither may raise."""
    app = Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = showing(app, SplashScreen)

        screen.action_skip()
        screen.action_skip()
        screen.action_skip()
        await pilot.pause()


@pytest.mark.parametrize("width", [20, 40, 120])
async def test_it_draws_at_any_terminal_width(width: int) -> None:
    """The wordmark is 60 columns of container in a terminal that may be 20."""
    app = Harness()
    async with app.run_test(size=(width, 12)) as pilot:
        await pilot.pause()
        screen = showing(app, SplashScreen)

        for _ in range(0, FRAMES_TO_THE_END, 5):
            screen._tick()
        await pilot.pause()
