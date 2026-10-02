"""The two keys: `n` records work that was not clocked, `N` reviews it."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest
from textual.pilot import Pilot
from textual.widgets import Digits, Input, Static

from flexi.app import FlexiApp
from flexi.components.expandable import ExpandableTable, RowKind
from flexi.config import CONFIG
from flexi.constants import DayKind
from flexi.domain.format import digits as digits_of
from flexi.domain.punch import Cell, strip
from flexi.screens.modals import CorrectionModal, CorrectionsModal
from flexi.services.registry import invalidate_services
from tests.tui.conftest import (
    WIDE,
    AppFactory,
    dashboard,
    screen_text,
    showing,
    status_text,
)


def table(app: FlexiApp) -> ExpandableTable:
    return app.screen.query_one("#records-table", ExpandableTable)


async def record(pilot: Pilot[None], opened: str, closed: str) -> None:
    """Open the correction modal, fill it in, and confirm."""
    await pilot.press(CONFIG.hotkeys.new_session)
    await pilot.pause()
    modal = pilot.app.screen
    modal.query_one("#correction-from", Input).value = opened
    modal.query_one("#correction-to", Input).value = closed
    await pilot.press("enter")
    await pilot.pause()
    await pilot.pause()


async def test_work_is_recorded_on_the_period_anchor(
    app_factory: AppFactory,
) -> None:
    """With the table unfocused there is no cursor, so the anchor answers."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        when = dashboard(app).period.anchor
        before = app.services.ledger.day(when).worked

        await record(pilot, "6:00", "7:30")

        after = app.services.ledger.day(when).worked
        assert after - before == timedelta(hours=1, minutes=30)
        assert "Recorded" in status_text(app)


async def test_work_is_recorded_under_the_cursor(
    app_factory: AppFactory,
) -> None:
    """The modal title names the day, so it has to be the day that is written."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        when = date(2026, 6, 8)  # a Monday, and not the anchor
        assert dashboard(app).period.anchor != when
        before = app.services.ledger.day(when).worked

        widget = table(app)
        widget.focus()
        widget.focus_key(f"{RowKind.DAY}{when.isoformat()}")
        await pilot.pause()

        await pilot.press(CONFIG.hotkeys.new_session)
        await pilot.pause()
        assert showing(app, CorrectionModal)._day == when

        modal = app.screen
        modal.query_one("#correction-from", Input).value = "6:00"
        modal.query_one("#correction-to", Input).value = "7:30"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        after = app.services.ledger.day(when).worked
        assert after - before == timedelta(hours=1, minutes=30)


async def test_row_with_no_day_falls_back_to_the_anchor(
    app_factory: AppFactory,
) -> None:
    """The last row is the period's total and belongs to no day."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        widget = table(app)
        widget.focus()
        widget.focus_key("t-period")
        await pilot.pause()

        await pilot.press(CONFIG.hotkeys.new_session)
        await pilot.pause()

        assert showing(app, CorrectionModal)._day == dashboard(app).period.anchor


async def test_correction_is_drawn_apart_from_a_punch(
    app_factory: AppFactory,
) -> None:
    """The times are inside the drawn window, so the assertion turns on the fill."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        when = dashboard(app).period.anchor
        await record(pilot, "7:00", "8:00")

        ledger = app.services.ledger.day(when)
        drawn = strip(
            ledger,
            60,
            app.services.ledger.window,
            now=datetime.combine(when, time(23, 0), tzinfo=UTC),
        )

        assert Cell.AMENDED in drawn
        assert Cell.ON in drawn, "the punched session is still drawn as one"


async def test_refusal_is_reported_and_not_written(
    app_factory: AppFactory,
) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        when = dashboard(app).period.anchor
        before = app.services.ledger.day(when).worked

        await record(pilot, "17:00", "9:00")

        assert "ends before it starts" in status_text(app)
        assert app.services.ledger.day(when).worked == before


async def test_unreadable_time_keeps_the_modal_open(
    app_factory: AppFactory,
) -> None:
    """What was typed stays on screen beside what was wrong with it."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press(CONFIG.hotkeys.new_session)
        await pilot.pause()
        modal = showing(app, CorrectionModal)
        modal.query_one("#correction-from", Input).value = "elevenish"
        modal.query_one("#correction-to", Input).value = "17:00"

        await pilot.press("enter")
        await pilot.pause()

        showing(app, CorrectionModal)
        assert "elevenish" in str(modal.query_one("#modal-error", Static).render())


async def test_time_made_of_markup_is_quoted_back(
    app_factory: AppFactory,
) -> None:
    """`[/]` is a closing tag with nothing to close, and Rich raises `MarkupError`."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press(CONFIG.hotkeys.new_session)
        await pilot.pause()
        modal = showing(app, CorrectionModal)
        modal.query_one("#correction-from", Input).value = "[/]"
        modal.query_one("#correction-to", Input).value = "17:00"

        await pilot.press("enter")
        await pilot.pause()

        assert app._exception is None
        showing(app, CorrectionModal)
        assert "[/]" in str(modal.query_one("#modal-error", Static).render())


async def test_empty_field_is_asked_for_again(
    app_factory: AppFactory,
) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press(CONFIG.hotkeys.new_session)
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()

        showing(app, CorrectionModal)


async def test_examples_read_as_examples(app_factory: AppFactory) -> None:
    """A bare `9:00` in grey looks like a time already given, as setup's do."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press(CONFIG.hotkeys.new_session)
        await pilot.pause()

        modal = showing(app, CorrectionModal)
        assert modal.query_one("#correction-from", Input).placeholder == "e.g. 9:00"
        assert modal.query_one("#correction-to", Input).placeholder == "e.g. 17:00"


async def test_enter_after_the_start_moves_to_the_end(
    app_factory: AppFactory,
) -> None:
    """A start with no end is half an answer, so enter asks for the other half."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        when = dashboard(app).period.anchor
        before = app.services.ledger.day(when).worked
        await pilot.press(CONFIG.hotkeys.new_session)
        await pilot.pause()
        modal = showing(app, CorrectionModal)
        modal.query_one("#correction-from", Input).value = "8:30"

        await pilot.press("enter")
        await pilot.pause()

        assert showing(app, CorrectionModal) is modal
        assert app.focused is modal.query_one("#correction-to", Input)
        assert str(modal.query_one("#modal-error", Static).render()) == ""
        assert app.services.ledger.day(when).worked == before


@pytest.mark.parametrize(
    ("opened", "closed", "said"),
    [
        ("", "17:00", "Type the time you started, like 9:00"),
        ("8:30", "", "Type the time you finished, like 17:00"),
    ],
    ids=["no start", "no end"],
)
async def test_empty_time_says_what_to_type(
    app_factory: AppFactory, opened: str, closed: str, said: str
) -> None:
    """Asked from the last field, where enter has nowhere further to go."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press(CONFIG.hotkeys.new_session)
        await pilot.pause()
        modal = showing(app, CorrectionModal)
        modal.query_one("#correction-from", Input).value = opened
        modal.query_one("#correction-to", Input).value = closed
        modal.query_one("#correction-to", Input).focus()
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()

        assert showing(app, CorrectionModal) is modal
        assert str(modal.query_one("#modal-error", Static).render()) == said


async def test_review_lists_the_corrections_in_the_period(
    app_factory: AppFactory,
) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await record(pilot, "6:00", "7:30")

        await pilot.press(CONFIG.hotkeys.corrections)
        await pilot.pause()

        showing(app, CorrectionsModal)
        shown = screen_text(app)
        assert "06:00–07:30" in shown
        assert "1:30 recorded" in shown


async def test_review_says_when_there_is_nothing_to_review(
    app_factory: AppFactory,
) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()

        await pilot.press(CONFIG.hotkeys.corrections)
        await pilot.pause()

        showing(app, CorrectionsModal)
        assert "Nothing recorded after the fact" in screen_text(app)


async def test_review_closes_on_escape(app_factory: AppFactory) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press(CONFIG.hotkeys.corrections)
        await pilot.pause()

        await pilot.press("escape")
        await pilot.pause()

        assert not isinstance(app.screen, CorrectionsModal)


async def test_cancelling_the_correction_writes_nothing(
    app_factory: AppFactory,
) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        when = dashboard(app).period.anchor
        before = app.services.ledger.day(when).worked

        await pilot.press(CONFIG.hotkeys.new_session)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert app.services.ledger.day(when).worked == before


async def test_recording_a_correction_moves_the_balance_on_screen(
    app_factory: AppFactory,
) -> None:
    """Read off the widget: the arithmetic and the redraw are two separate things.

    Long enough to take today past its hours: short of them, it is still owed
    nothing, and the balance stands where it did.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        digits = app.screen.query_one("#balance-digits", Digits)
        before = digits.value

        await record(pilot, "5:00", "8:00")

        assert digits.value != before, "the readout still shows the old balance"
        summary = app.services.ledger.balance(dashboard(app).now.date())
        assert digits.value == digits_of(summary.delta)


# ---- before setup ----

SET_UP = date(2026, 6, 11)
"""The seeded Thursday, standing in for the day Flexi was set up."""


def track_from(app: FlexiApp, since: date, *, contracted_minutes: int = 444) -> None:
    """Move the tracking stamp, and the contract with it, under the open app."""
    stored = app.services.settings.get_settings()
    assert stored is not None
    stored.tracking_since = since
    stored.contracted_minutes = contracted_minutes
    invalidate_services(app.services)


async def open_on(app: FlexiApp, pilot: Pilot[None], when: date) -> str:
    """Open the correction modal on a date and read its caption."""
    screen = dashboard(app)
    screen.set_period(screen.period.go_to(when))
    await pilot.pause()
    await pilot.press(CONFIG.hotkeys.new_session)
    await pilot.pause()
    return str(showing(app, CorrectionModal).query_one(".caption", Static).render())


async def test_a_day_before_setup_says_it_will_owe_the_contracted_day(
    app_factory: AppFactory,
) -> None:
    """The length is the one set, so a 7:30 contract does not read as 7:24."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        track_from(app, SET_UP, contracted_minutes=450)

        caption = await open_on(app, pilot, date(2026, 6, 8))

        assert caption == (
            "Flexi started tracking on Thu 11 Jun. "
            "Work recorded here counts Mon 8 against your 7:30 day."
        )


async def test_an_empty_day_before_setup_warns_of_what_it_will_owe(
    app_factory: AppFactory,
) -> None:
    """Untracked while empty, the day expects nothing until the work is saved."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        track_from(app, SET_UP)
        when = date(2026, 4, 2)  # a Thursday before the seeded leave year
        assert app.services.ledger.day(when).kind is DayKind.UNTRACKED

        caption = await open_on(app, pilot, when)

        assert caption == (
            "Flexi started tracking on Thu 11 Jun. "
            "Work recorded here counts Thu 2 against your 7:24 day."
        )


async def test_a_half_day_off_before_setup_says_it_will_owe_the_other_half(
    app_factory: AppFactory,
) -> None:
    """The seeded Tuesday has its morning booked off, so it owes only the afternoon."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        track_from(app, SET_UP)

        caption = await open_on(app, pilot, date(2026, 6, 9))

        assert caption == (
            "Flexi started tracking on Thu 11 Jun. "
            "Work recorded here counts Tue 9 against your 3:42 day."
        )


@pytest.mark.parametrize(
    "when",
    [date(2026, 6, 6), date(2026, 5, 25), date(2026, 6, 5), SET_UP],
    ids=[
        "weekend-before-setup",
        "bank-holiday-before-setup",
        "booked-off-before-setup",
        "setup-day",
    ],
)
async def test_a_day_owing_nothing_new_keeps_the_usual_caption(
    app_factory: AppFactory, when: date
) -> None:
    """Only a working day before setup starts owing the contract when worked.

    A day booked off in full owes nothing, and refuses the work besides.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        track_from(app, SET_UP)

        caption = await open_on(app, pilot, when)

        assert caption.startswith("For a day you worked and did not clock.")
