"""Feature 1: clocking in and out is one key, and it is recorded."""

from __future__ import annotations

from datetime import UTC, datetime

from textual.widgets import Button, Input, Switch

from flexi.components.modules.clock import ClockModule
from flexi.constants import ClockAction
from flexi.messages import Scope
from flexi.models.database.db import ClockEvent
from flexi.screens.dashboard import with_time
from flexi.services.absence import AbsenceResult
from flexi.services.clock import ClockResult
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


async def test_a_module_that_writes_tells_the_screen_and_the_live_tick_follows(
    app_factory: AppFactory,
) -> None:
    """A module never redraws its neighbours; it announces, and the screen does.

    The announcement is the whole contract between the panels and the screen:
    the ledger is invalidated once, the interested modules rebuild, and the
    one-second tick is started or stopped. Without that last part a session
    closed from a panel leaves a timer redrawing a clock that has stopped.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = dashboard(app)
        assert screen._tick is not None, "the seed's open session should be ticking"

        app.services.clock.clock_out()  # written behind the screen's back
        app.screen.query_one(ClockModule).announce(Scope.CLOCK)
        await pilot.pause()

        assert str(app.screen.query_one("#clock-button", Button).label) == "Arrive"
        assert screen._tick is None, "a closed session left the timer running"


# -- stamping the status line ------------------------------------------------

STAMPED_AT = datetime(2026, 6, 11, 12, 4, tzinfo=UTC)


def punched(message: str) -> ClockResult:
    """A clock result carrying the event every clock-out return carries."""
    return ClockResult(
        success=True,
        message=message,
        event=ClockEvent(action=ClockAction.OUT, timestamp=STAMPED_AT),
    )


def test_a_clock_message_is_stamped_with_the_moment_it_recorded() -> None:
    """A stamped message is a fact somebody can check against the wall clock."""
    assert with_time("Clocked out", punched("Clocked out")).endswith("at 12:04")


def test_a_result_that_is_not_a_clocking_is_left_alone() -> None:
    """A discarded session carries an event and must not be stamped with it.

    "Discarded — under 1 minute on the clock at 12:04" reads as a discard that
    happened at 12:04, which is not what the sentence is about — and both
    returns that say something other than "Clocked" carry an event, so the
    presence of one cannot be the test.
    """
    discarded = "Discarded — under 1 minute on the clock"
    assert with_time(discarded, punched(discarded)) == discarded

    backwards = "That clock-out is earlier than the clock-in"
    assert with_time(backwards, punched(backwards)) == backwards


def test_a_result_from_somewhere_other_than_the_clock_is_left_alone() -> None:
    """Absence and adjustment results reach the same status bar."""
    assert with_time("Booked 3 days", AbsenceResult(True, "Booked 3 days")) == (
        "Booked 3 days"
    )
