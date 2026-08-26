"""The frame every modal is built on, and the ways one says no.

`AbsenceModal` is exercised through the screens that open it elsewhere; what is
here is the base class's own behaviour — the frame a modal gets for free, the
pointer as an alternative to the keyboard, and each refusal that keeps a dialog
open with what was typed still on screen.
"""

from __future__ import annotations

from datetime import date, timedelta

from textual.app import ComposeResult
from textual.widgets import Button, Input, RadioSet, Static

from flexi.components.common import Rule
from flexi.constants import AbsenceType
from flexi.screens.help import HelpScreen
from flexi.screens.modals import AbsenceModal, FlexiModal, selected_name
from tests.tui.conftest import WIDE, AppFactory, showing

FREE_MONDAY = date(2026, 6, 22)  # nothing booked on it in the seed


# -- the frame -------------------------------------------------------------


async def test_a_modal_that_asks_nothing_still_gets_the_frame(
    app_factory: AppFactory,
) -> None:
    """The base class is the dialog: a title, a place to say no, and two buttons.

    Fields are the part a subclass supplies, so a modal that collects nothing —
    a warning, a "this will take a moment" — is a valid modal rather than one
    that has to be told there is nothing to compose.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        app.push_screen(FlexiModal())
        await pilot.pause()

        modal = showing(app, FlexiModal)
        assert not modal.query(Input), "nothing to fill in"
        assert str(modal.query_one("#modal-error", Static).render()) == ""
        assert str(modal.query_one("#modal-cancel", Button).label) == "Cancel"
        assert str(modal.query_one("#modal-confirm", Button).label) == "Save"


async def test_the_buttons_answer_the_modal_as_the_keys_do(
    app_factory: AppFactory,
) -> None:
    """Enter is faster; a pointer still has to be able to finish the job.

    Both buttons go through the same two actions the keys do, so there is one
    definition of what confirming means rather than a second one behind the
    mouse.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("A")
        await pilot.pause()
        modal = showing(app, AbsenceModal)
        modal.query_one("#absence-date", Input).value = FREE_MONDAY.isoformat()
        await pilot.pause()

        await pilot.click("#modal-confirm")
        await pilot.pause()
        await pilot.pause()

        assert not isinstance(app.screen, AbsenceModal)
        assert len(app.services.absence.for_date(FREE_MONDAY)) == 1


async def test_the_cancel_button_writes_nothing(app_factory: AppFactory) -> None:
    """The quiet button is the one that has to be trustworthy.

    Everything but the confirm button cancels, so a button added to the row
    tomorrow cannot accidentally inherit "yes".
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("A")
        await pilot.pause()
        modal = showing(app, AbsenceModal)
        modal.query_one("#absence-date", Input).value = FREE_MONDAY.isoformat()
        await pilot.pause()

        await pilot.click("#modal-cancel")
        await pilot.pause()
        await pilot.pause()

        assert not isinstance(app.screen, AbsenceModal)
        assert app.services.absence.for_date(FREE_MONDAY) == []


# -- what the booking modal refuses ----------------------------------------


async def test_a_date_that_cannot_be_read_keeps_the_modal_open(
    app_factory: AppFactory,
) -> None:
    """The error goes under the fields, not over them.

    What was typed stays on screen next to what was wrong with it — a dialog
    that closed on a typo would take the other four answers with it.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("A")
        await pilot.pause()
        modal = showing(app, AbsenceModal)
        modal.query_one("#absence-date", Input).value = "whenever"
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()

        assert app.screen is modal, "the modal should still be open"
        assert modal.query_one("#absence-date", Input).value == "whenever"
        assert "Try" in str(modal.query_one("#modal-error", Static).render())


async def test_a_last_day_that_cannot_be_read_is_refused_too(
    app_factory: AppFactory,
) -> None:
    """Both ends of a span are typed, so both ends can be mistyped.

    The first field was read and the second was not, which is how a fortnight
    came to be booked from a date nobody had checked.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        app.push_screen(AbsenceModal(FREE_MONDAY, until=FREE_MONDAY + timedelta(4)))
        await pilot.pause()
        modal = showing(app, AbsenceModal)
        modal.query_one("#absence-until", Input).value = "whenever"
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()

        assert app.screen is modal, "the modal should still be open"
        assert "Try" in str(modal.query_one("#modal-error", Static).render())
        assert (
            app.services.absence.in_range(FREE_MONDAY, FREE_MONDAY + timedelta(4)) == []
        )


class Unanswered(FlexiModal[str]):
    """A modal whose radio set has nothing pressed in it.

    Every radio set Flexi composes starts with one button on, so this is the
    state the fallback exists for and the only way to sit in it.
    """

    def compose_body(self) -> ComposeResult:
        yield RadioSet(id="pick-one")

    def result(self) -> str:
        return selected_name(self, "#pick-one", fallback=AbsenceType.ANNUAL.value)


async def test_nothing_pressed_in_a_radio_set_answers_with_the_fallback(
    app_factory: AppFactory,
) -> None:
    """A question with no answer on it still has to produce one.

    The enum value lives in the pressed button's ``name``, so there is nothing
    to read when nothing is pressed — and a dialog that raised on the way out
    would strand whatever else had been filled in.
    """
    app = app_factory()
    collected: list[str | None] = []
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        app.push_screen(Unanswered(), callback=collected.append)
        await pilot.pause()
        assert showing(app, Unanswered).query_one(RadioSet).pressed_button is None

        await pilot.press("enter")
        await pilot.pause()

        assert collected == [AbsenceType.ANNUAL.value]


# -- the allowance line ----------------------------------------------------


def test_a_modal_told_no_figures_says_nothing_about_them() -> None:
    """The hint is context, and an absent figure is not context.

    Opened from a place that has not worked out what is left — or in a Flexi
    with no entitlement recorded at all — the line is empty rather than
    "None days annual leave left".
    """
    assert AbsenceModal(FREE_MONDAY)._allowance_hint() == ""


def test_a_modal_told_one_figure_says_only_that_one() -> None:
    """Each half of the line stands on its own, joined only when both are there."""
    remaining_only = AbsenceModal(FREE_MONDAY, remaining=3.5)
    toil_only = AbsenceModal(FREE_MONDAY, toil_days=1.0)

    assert remaining_only._allowance_hint() == "3.5 days annual leave left"
    assert toil_only._allowance_hint() == "1 day of TOIL banked"


# -- the help modal --------------------------------------------------------


async def test_a_group_with_no_keys_in_it_is_not_given_a_heading(
    app_factory: AppFactory,
) -> None:
    """A heading with nothing under it is a widget somebody has to rule out.

    The bindings are collected from whatever is on screen, so which groups have
    anything in them is a property of the moment help was asked for.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        app.push_screen(
            HelpScreen({"Dashboard": [("d", "Do the thing")], "Records": []})
        )
        await pilot.pause()

        headings = [str(rule.render()) for rule in showing(app, HelpScreen).query(Rule)]
        assert headings == ["Dashboard"]
