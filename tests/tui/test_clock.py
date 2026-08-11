"""Feature 1: clocking in and out is one key, and it is recorded."""

from __future__ import annotations

from textual.widgets import Button, Input, Switch

from flexi.components.modules.clock import ClockModule
from flexi.messages import Scope
from tests.tui.conftest import WIDE, AppFactory, dashboard, status_text


async def test_slash_clocks_out_and_back_in(app_factory: AppFactory) -> None:
    """It toggles, from the dashboard, with one unshifted key.

    There is no way to clock in twice from here — the key is a toggle — which is
    why the test that claimed to check that refusal at this layer has gone. It
    booted the application and then called the service directly, asserting a
    branch `tests/services/test_clock.py` already asserts in milliseconds.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        assert app.services.clock.is_clocked_in()  # the seed leaves a session open

        await pilot.press("slash")
        await pilot.pause()
        assert not app.services.clock.is_clocked_in()
        assert "Clocked out" in status_text(app)

        await pilot.press("slash")
        await pilot.pause()
        assert app.services.clock.is_clocked_in()
        assert "Clocked in" in status_text(app)


async def test_the_button_does_the_same_thing(app_factory: AppFactory) -> None:
    """It works for a pointer, because the point of Textual is that one works."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        button = app.screen.query_one("#clock-button", Button)
        assert str(button.label) == "Depart"

        await pilot.click("#clock-button")
        await pilot.pause()
        assert not app.services.clock.is_clocked_in()
        assert str(app.screen.query_one("#clock-button", Button).label) == "Arrive"


async def test_the_switch_reflects_the_truth_without_looping(
    app_factory: AppFactory,
) -> None:
    """It writes the switch back on every redraw without treating that as input.

    A naive handler acts on the write, clocks straight back out, redraws, and
    does it again — a loop that ends in a toast on every tick.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        switch = app.screen.query_one("#clock-switch", Switch)
        assert switch.value is True

        await pilot.press("slash")
        await pilot.pause()
        assert app.screen.query_one("#clock-switch", Switch).value is False
        assert not app.services.clock.is_clocked_in()

        # A redraw writes the switch back from the database. If that write were
        # treated as a user action the clock would flip again here.
        dashboard(app).refresh_modules(Scope.ALL)
        await pilot.pause()
        assert not app.services.clock.is_clocked_in()


async def test_slash_does_not_reach_a_focused_input(app_factory: AppFactory) -> None:
    """It gives the key to the field, so a typed date can contain a slash."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("g")  # go-to-date modal
        await pilot.pause()
        field = app.screen.query_one("#goto-input", Input)
        field.focus()
        await pilot.pause()

        before = app.services.clock.is_clocked_in()
        await pilot.press("slash")
        await pilot.pause()

        assert app.services.clock.is_clocked_in() is before
        assert "/" in field.value


async def test_the_switch_moves_through_its_watcher_so_it_animates(
    app_factory: AppFactory,
) -> None:
    """It sets the reactive rather than writing past it.

    `set_reactive` puts the value in without running the watcher, and the
    watcher is what slides the slider — the clock moved and the switch sat
    still. Asserting on the animation itself would be asserting on a tween, so
    this asserts on the thing that drives it: the reactive changed, and the
    widget's own watcher saw it.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        switch = app.screen.query_one("#clock-switch", Switch)
        seen: list[bool] = []
        original = switch.watch_value

        def record(value: bool) -> None:
            seen.append(value)
            original(value)

        switch.watch_value = record  # type: ignore[method-assign]

        await pilot.press("slash")
        await pilot.pause()

        assert seen == [False], (
            "the watcher should have run exactly once, with the new value"
        )


async def test_the_elapsed_time_is_in_the_border_subtitle(
    app_factory: AppFactory,
) -> None:
    """It puts the live figure in the module's data slot, not in a whole row."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        clock = app.screen.query_one(ClockModule)
        assert ":" in str(clock.border_subtitle)

        await pilot.press("slash")  # clock out
        await pilot.pause()
        assert str(app.screen.query_one(ClockModule).border_subtitle) == "/"
