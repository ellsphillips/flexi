"""The frame every modal is built on, and the ways one says no.

The screens that open `AbsenceModal` exercise its own behaviour; here it
stands in for the base class: the frame, the pointer as an alternative to the
keyboard, and each refusal that keeps a dialog open with what was typed on it.
"""

from __future__ import annotations

from datetime import date, timedelta

from textual.app import ComposeResult
from textual.widgets import Button, Input, RadioSet, Static

from flexi.components.common import Rule
from flexi.constants import AbsenceType, Portion
from flexi.screens.help import HelpScreen
from flexi.screens.modals import AbsenceModal, FlexiModal, selected_name
from tests.tui.conftest import WIDE, AppFactory, showing

FREE_MONDAY = date(2026, 6, 22)  # nothing booked on it in the seed


# ---- the frame ----


async def test_modal_that_asks_nothing_gets_the_frame(
    app_factory: AppFactory,
) -> None:
    """The base class is the dialog: a title, a place to say no, two buttons.

    Fields are the part a subclass supplies, so a modal that collects nothing
    is still a modal.
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


async def test_buttons_answer_as_the_keys_do(
    app_factory: AppFactory,
) -> None:
    """Both buttons go through the same two actions the keys do."""
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


async def test_cancel_button_writes_nothing(app_factory: AppFactory) -> None:
    """Every button but confirm cancels, so a new one cannot inherit "yes"."""
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


# ---- what the booking modal refuses ----


async def test_unreadable_date_keeps_the_modal_open(
    app_factory: AppFactory,
) -> None:
    """The error goes under the fields, and what was typed stays on screen."""
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


async def test_unreadable_last_day_is_refused_too(
    app_factory: AppFactory,
) -> None:
    """Both ends of a span are typed, so both ends can be mistyped."""
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
    only way to sit in the state the fallback exists for.
    """

    def compose_body(self) -> ComposeResult:
        yield RadioSet(id="pick-one")

    def result(self) -> str:
        return selected_name(self, "#pick-one", fallback=AbsenceType.ANNUAL.value)


async def test_empty_radio_set_answers_with_fallback(
    app_factory: AppFactory,
) -> None:
    """The enum value lives in the pressed button's ``name``.

    With nothing pressed there is nothing to read, and raising on the way out
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


# ---- the allowance line ----


def test_modal_told_no_figures_stays_silent() -> None:
    """An absent figure is not context, so the line is empty."""
    assert AbsenceModal(FREE_MONDAY)._allowance_hint() == ""


def test_modal_told_one_figure_says_one() -> None:
    """Each half of the line stands on its own, joined only when both are there."""
    remaining_only = AbsenceModal(FREE_MONDAY, remaining=3.5)
    toil_only = AbsenceModal(FREE_MONDAY, toil_days=1.0)

    assert remaining_only._allowance_hint() == "3.5 days annual leave left"
    assert toil_only._allowance_hint() == "1 day of TOIL banked"


# ---- the help modal ----


async def test_empty_group_gets_no_heading(
    app_factory: AppFactory,
) -> None:
    """Bindings are collected from what is on screen, so a group can be bare."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        app.push_screen(
            HelpScreen({"Dashboard": [("d", "Do the thing")], "Records": []})
        )
        await pilot.pause()

        headings = [str(rule.render()) for rule in showing(app, HelpScreen).query(Rule)]
        assert headings == ["Dashboard"]


# ---- enter, and what has focus when it is pressed ----


async def test_enter_on_the_cancel_button_cancels(app_factory: AppFactory) -> None:
    """The enter binding stands down while any button but Confirm has focus."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("A")
        await pilot.pause()
        modal = showing(app, AbsenceModal)
        modal.query_one("#absence-date", Input).value = FREE_MONDAY.isoformat()
        modal.query_one("#modal-cancel", Button).focus()
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        assert not isinstance(app.screen, AbsenceModal)
        assert app.services.absence.for_date(FREE_MONDAY) == []


async def test_enter_on_the_confirm_button_still_confirms(
    app_factory: AppFactory,
) -> None:
    """The one button enter is allowed to answer for."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("A")
        await pilot.pause()
        modal = showing(app, AbsenceModal)
        modal.query_one("#absence-date", Input).value = FREE_MONDAY.isoformat()
        modal.query_one("#modal-confirm", Button).focus()
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        assert not isinstance(app.screen, AbsenceModal)
        assert len(app.services.absence.for_date(FREE_MONDAY)) == 1


async def test_enter_in_a_field_confirms(app_factory: AppFactory) -> None:
    """Nothing but a button stands the binding down, so a field still submits."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("A")
        await pilot.pause()
        modal = showing(app, AbsenceModal)
        modal.query_one("#absence-date", Input).value = FREE_MONDAY.isoformat()
        modal.query_one("#absence-note", Input).focus()
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        assert len(app.services.absence.for_date(FREE_MONDAY)) == 1


# ---- the radio sets ----


async def test_arrowing_to_a_type_books_the_type_arrowed_to(
    app_factory: AppFactory,
) -> None:
    """The highlight and the answer move together."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("A")
        await pilot.pause()
        modal = showing(app, AbsenceModal)
        modal.query_one("#absence-date", Input).value = FREE_MONDAY.isoformat()
        modal.query_one("#absence-type", RadioSet).focus()
        await pilot.pause()

        await pilot.press("down")
        await pilot.pause()
        assert selected_name(modal, "#absence-type", fallback="?") == "sick"

        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        booked = app.services.absence.for_date(FREE_MONDAY)
        assert [row.absence_type for row in booked] == [AbsenceType.SICK]


async def test_arrowing_back_to_a_portion_books_that_portion(
    app_factory: AppFactory,
) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("A")
        await pilot.pause()
        modal = showing(app, AbsenceModal)
        modal.query_one("#absence-date", Input).value = FREE_MONDAY.isoformat()
        modal.query_one("#absence-portion", RadioSet).focus()
        await pilot.pause()

        await pilot.press("up")
        await pilot.pause()
        assert selected_name(modal, "#absence-portion", fallback="?") == "pm"

        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        assert app.services.absence.for_date(FREE_MONDAY)[0].portion is Portion.PM
