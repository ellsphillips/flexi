"""The leave screen: a year you move a cursor over and book on."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from textual.pilot import Pilot
from textual.widgets import Button, Input, Static

from flexi.app import FlexiApp
from flexi.components.common import Gauge, Tone
from flexi.components.yearcalendar import YearCalendar
from flexi.constants import AbsenceType, Portion, Verdict
from flexi.messages import Scope
from flexi.screens.leave import LeaveScreen, preview
from flexi.screens.modals import (
    AbsenceModal,
    ConfirmModal,
    GoToDateModal,
    selected_name,
)
from flexi.services.absence import (
    PLAN_CHANGED,
    AbsencePlan,
    AnnualBalance,
    PlannedDay,
)
from flexi.services.settings import SettingsUpdate
from tests.tui.conftest import WIDE, AppFactory, screen_text, showing, status_text

TODAY = date(2026, 6, 11)  # a Thursday
FREE_MONDAY = date(2026, 6, 22)  # nothing booked on it in the seed
SATURDAY = date(2026, 6, 20)  # not a working day in the seed's pattern


def calendar(app: FlexiApp) -> YearCalendar:
    return app.screen.query_one(YearCalendar)


async def open_leave(pilot: Pilot[None]) -> None:
    await pilot.press("f2")
    await pilot.pause()


# ---- getting there ----


async def test_f2_opens_the_leave_year(app_factory: AppFactory) -> None:
    """Opens on the leave year, with the cursor on today."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        assert showing(app, LeaveScreen).period.start == date(2026, 4, 6)
        assert calendar(app).selection.head == TODAY


async def test_escape_leaves(app_factory: AppFactory) -> None:
    """With nothing selected, escape is the way out.

    The calendar binds it too, to collapse a selection, and a focused widget
    is asked first, so it stands the binding down with nothing to collapse.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, LeaveScreen)


async def test_whole_year_is_laid_out(app_factory: AppFactory) -> None:
    """Thirteen months, because a leave year starting on the 6th touches both ends."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        assert len(calendar(app).blocks) == 13


# ---- moving ----


@pytest.mark.parametrize(
    ("key", "days"),
    [("right", 1), ("left", -1), ("down", 7), ("up", -7), ("l", 1), ("k", -7)],
)
async def test_cursor_moves(app_factory: AppFactory, key: str, days: int) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        await pilot.press(key)
        await pilot.pause()
        assert calendar(app).selection.head == TODAY + timedelta(days=days)


async def test_shift_extends_and_escape_collapses(app_factory: AppFactory) -> None:
    """A selection is an anchor and a head, so it can be pulled back."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        await pilot.press("shift+right", "shift+right")
        await pilot.pause()
        assert len(calendar(app).selection) == 3

        await pilot.press("escape")
        await pilot.pause()
        assert calendar(app).selection.single
        assert isinstance(app.screen, LeaveScreen), "escape collapsed, it did not leave"


async def test_month_step_clamps_to_a_shorter_month(app_factory: AppFactory) -> None:
    """From the 31st into a 30-day month lands on the 30th, not nowhere."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        calendar(app).go_to(date(2026, 7, 31))
        await pilot.pause()
        await pilot.press("right_square_bracket")
        await pilot.pause()
        assert calendar(app).selection.head == date(2026, 8, 31)
        await pilot.press("right_square_bracket")
        await pilot.pause()
        assert calendar(app).selection.head == date(2026, 9, 30)


async def test_home_and_end_stay_inside_the_year(
    app_factory: AppFactory,
) -> None:
    """The grid draws whole months, so its first drawn day is the year before.

    Landing there would take the screen off the leave year every figure beside
    the calendar is measured over.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        year = showing(app, LeaveScreen).period

        await pilot.press("home")
        await pilot.pause()
        assert calendar(app).selection.head == date(2026, 4, 6)
        assert showing(app, LeaveScreen).period == year

        await pilot.press("end")
        await pilot.pause()
        assert calendar(app).selection.head == date(2027, 4, 5)
        assert showing(app, LeaveScreen).period == year


async def test_t_brings_the_cursor_back_to_today(app_factory: AppFactory) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        calendar(app).go_to(date(2026, 10, 14))
        await pilot.pause()

        await pilot.press("t")
        await pilot.pause()
        assert calendar(app).selection.head == TODAY


async def test_going_to_another_leave_year_reloads_it(
    app_factory: AppFactory,
) -> None:
    """The grid holds one leave year, so leaving it has to redraw it."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)

        await pilot.press("g")
        await pilot.pause()
        showing(app, GoToDateModal).query_one("#goto-input", Input).value = "2028-06-14"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        leave = showing(app, LeaveScreen)
        assert leave.period.start == date(2028, 4, 6)
        assert calendar(app).selection.head == date(2028, 6, 14)
        assert calendar(app).border_subtitle == "nothing booked", (
            "an unbooked year says so, not '0 days booked'"
        )


async def test_going_to_a_date_in_this_year_only_moves(
    app_factory: AppFactory,
) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        before = showing(app, LeaveScreen).period

        await pilot.press("g")
        await pilot.pause()
        showing(app, GoToDateModal).query_one("#goto-input", Input).value = "2026-12-01"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        assert showing(app, LeaveScreen).period == before
        assert calendar(app).selection.head == date(2026, 12, 1)


async def test_cancelling_go_to_date_leaves_the_cursor_alone(
    app_factory: AppFactory,
) -> None:
    """A cancelled prompt hands the callback `None`, and the cursor stays put."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        calendar(app).go_to(date(2026, 10, 14))
        await pilot.pause()

        await pilot.press("g")
        await pilot.pause()
        showing(app, GoToDateModal).query_one("#goto-input", Input).value = "2026-12-25"
        await pilot.press("escape")
        await pilot.pause()
        await pilot.pause()

        showing(app, LeaveScreen)
        assert calendar(app).selection.head == date(2026, 10, 14)


# ---- booking ----


async def test_one_key_books_a_day(app_factory: AppFactory) -> None:
    """No modal: the cursor is the subject, and `x` takes the day back."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        calendar(app).go_to(FREE_MONDAY)
        await pilot.pause()

        await pilot.press("A")
        await pilot.pause()

        assert not isinstance(app.screen, ConfirmModal), "no dialog for one day"
        booked = app.services.absence.for_date(FREE_MONDAY)
        assert [row.absence_type for row in booked] == [AbsenceType.ANNUAL]
        assert "1 day" in status_text(app)


async def test_one_key_books_a_range(app_factory: AppFactory) -> None:
    """A span is previewed before any of it is written."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        calendar(app).go_to(FREE_MONDAY)
        await pilot.pause()
        for _ in range(6):
            await pilot.press("shift+right")
        await pilot.pause()

        await pilot.press("A")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal), "a span asks first"
        assert (
            app.services.absence.in_range(FREE_MONDAY, FREE_MONDAY + timedelta(days=6))
            == []
        ), "and writes nothing until it is answered"

        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        booked = app.services.absence.in_range(
            FREE_MONDAY, FREE_MONDAY + timedelta(days=6)
        )
        assert len(booked) == 5
        assert "5 days" in status_text(app)


async def test_preview_says_what_it_will_skip(
    app_factory: AppFactory,
) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        calendar(app).go_to(FREE_MONDAY)
        await pilot.pause()
        for _ in range(6):
            await pilot.press("shift+right")
        await pilot.pause()

        await pilot.press("A")
        await pilot.pause()

        shown = screen_text(app)
        assert "5 days of 7" in shown, shown
        assert "non-working" in shown, "the two it will pass over"
        assert "Annual leave:" in shown, "and what it costs"

        await pilot.press("escape")
        await pilot.pause()
        assert (
            app.services.absence.in_range(FREE_MONDAY, FREE_MONDAY + timedelta(days=6))
            == []
        ), "declining writes nothing"


async def test_space_cycles_the_portion_before_booking(app_factory: AppFactory) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        calendar(app).go_to(FREE_MONDAY)
        await pilot.pause()

        await pilot.press("space")
        await pilot.pause()
        assert showing(app, LeaveScreen).portion is Portion.AM

        await pilot.press("A")
        await pilot.pause()
        assert app.services.absence.for_date(FREE_MONDAY)[0].portion is Portion.AM


async def test_other_absence_goes_through_the_modal(app_factory: AppFactory) -> None:
    """`Other` needs a note, and the modal opens on the type that was pressed."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        calendar(app).go_to(FREE_MONDAY)
        await pilot.pause()
        before = app.services.absence.get_remaining_annual_leave()

        await pilot.press("O")
        await pilot.pause()
        modal = showing(app, AbsenceModal)
        assert selected_name(modal, "#absence-type", fallback="?") == "other"

        modal.query_one("#absence-note", Input).value = "jury service"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        booked = app.services.absence.for_date(FREE_MONDAY)
        assert [row.absence_type for row in booked] == [AbsenceType.OTHER]
        assert app.services.absence.get_remaining_annual_leave() == before


async def test_modal_opens_on_the_cycled_portion(
    app_factory: AppFactory,
) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        calendar(app).go_to(FREE_MONDAY)
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()

        await pilot.press("e")
        await pilot.pause()
        modal = showing(app, AbsenceModal)
        assert selected_name(modal, "#absence-portion", fallback="?") == "am"
        assert selected_name(modal, "#absence-type", fallback="?") == "annual", (
            "`e` still opens on annual leave"
        )

        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        assert app.services.absence.for_date(FREE_MONDAY)[0].portion is Portion.AM


async def test_cancelling_the_modal_books_nothing(app_factory: AppFactory) -> None:
    """A cancelled modal hands the callback `None`, which is not an answer."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        calendar(app).go_to(FREE_MONDAY)
        await pilot.pause()

        await pilot.press("e")
        await pilot.pause()
        showing(app, AbsenceModal)
        await pilot.press("escape")
        await pilot.pause()
        await pilot.pause()

        showing(app, LeaveScreen)
        assert app.services.absence.for_date(FREE_MONDAY) == []


async def test_booking_a_weekend_books_nothing_and_says_so(
    app_factory: AppFactory,
) -> None:
    """A Saturday is not refused; it is not a day leave is spent on.

    Nothing is refused, so there is no refusal to quote back and the screen
    supplies the wording.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        calendar(app).go_to(SATURDAY)
        await pilot.pause()

        await pilot.press("A")
        await pilot.pause()

        assert app.services.absence.for_date(SATURDAY) == []
        assert status_text(app) == "Nothing to book in that selection"


async def test_booking_over_work_repeats_the_refusal(
    app_factory: AppFactory,
) -> None:
    """The plan names what is in the way, and the screen quotes it."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)  # the cursor opens on today, which has hours on it

        await pilot.press("A")
        await pilot.pause()

        assert app.services.absence.for_date(TODAY) == []
        assert status_text(app) == "There is recorded work in that part of the day"


async def test_wallet_moves_with_the_booking(app_factory: AppFactory) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        gauge = app.screen.query_one("#leave-gauge-annual", Gauge)
        before = gauge.value

        calendar(app).go_to(FREE_MONDAY)
        await pilot.pause()
        await pilot.press("A")
        await pilot.pause()

        assert before is not None
        after = showing(app, LeaveScreen).query_one("#leave-gauge-annual", Gauge)
        assert after.value == before + 1


async def test_planner_says_when_leave_runs_out(
    app_factory: AppFactory,
) -> None:
    """The gauge takes its tone from the allowance, so an empty one reads red."""

    def annual_tone() -> Tone:
        return app.screen.query_one("#leave-gauge-annual", Gauge).tone

    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        assert annual_tone() is Tone.OK

        screen = showing(app, LeaveScreen)
        app.services.settings.save_entitlement(screen.period.start.year, 0.0)
        screen.rebuild()
        await pilot.pause()

        assert annual_tone() is Tone.ERR


# ---- removing ----


async def test_removing_a_day_is_immediate(app_factory: AppFactory) -> None:
    """Below the threshold it is faster to undo than to confirm."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        calendar(app).go_to(FREE_MONDAY)
        await pilot.pause()
        await pilot.press("A")
        await pilot.pause()

        await pilot.press("x")
        await pilot.pause()
        assert app.services.absence.for_date(FREE_MONDAY) == []
        assert "removed" in status_text(app)


async def test_removing_a_lot_asks_first(app_factory: AppFactory) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        calendar(app).go_to(FREE_MONDAY)
        await pilot.pause()
        for _ in range(6):
            await pilot.press("shift+right")
        await pilot.pause()
        await pilot.press("A")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        await pilot.press("x")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal)
        assert "5 days of annual leave" in screen_text(app), "and says what would go"
        assert (
            len(
                app.services.absence.in_range(
                    FREE_MONDAY, FREE_MONDAY + timedelta(days=6)
                )
            )
            == 5
        )


async def test_agreeing_to_the_question_clears_the_lot(app_factory: AppFactory) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        calendar(app).go_to(FREE_MONDAY)
        await pilot.pause()
        for _ in range(6):
            await pilot.press("shift+right")
        await pilot.pause()
        await pilot.press("A")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        await pilot.press("x")
        await pilot.pause()
        showing(app, ConfirmModal)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        assert (
            app.services.absence.in_range(FREE_MONDAY, FREE_MONDAY + timedelta(days=6))
            == []
        )
        assert "removed" in status_text(app)


async def test_confirmation_ignores_a_booking_added_later(
    app_factory: AppFactory,
) -> None:
    """The modal approves the five shown rows, not a mutable calendar range."""
    app = app_factory()
    end = FREE_MONDAY + timedelta(days=6)
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        calendar(app).go_to(FREE_MONDAY)
        await pilot.pause()
        for _ in range(6):
            await pilot.press("shift+right")
        await pilot.pause()
        booked = app.services.absence.book_range(
            FREE_MONDAY,
            end,
            AbsenceType.ANNUAL,
            Portion.AM,
        )
        assert len(booked.booked) == 5

        await pilot.press("x")
        await pilot.pause()
        showing(app, ConfirmModal)

        added = app.services.absence.book(
            FREE_MONDAY,
            AbsenceType.SICK,
            Portion.PM,
        )
        assert added.success
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        assert len(app.services.absence.in_range(FREE_MONDAY, end)) == 6
        assert status_text(app) == PLAN_CHANGED


async def test_declining_the_question_keeps_every_day_of_it(
    app_factory: AppFactory,
) -> None:
    """Escaping the dialog is not a quiet yes."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        calendar(app).go_to(FREE_MONDAY)
        await pilot.pause()
        for _ in range(6):
            await pilot.press("shift+right")
        await pilot.pause()
        await pilot.press("A")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        await pilot.press("x")
        await pilot.pause()
        showing(app, ConfirmModal)
        await pilot.press("escape")
        await pilot.pause()
        await pilot.pause()

        showing(app, LeaveScreen)
        assert (
            len(
                app.services.absence.in_range(
                    FREE_MONDAY, FREE_MONDAY + timedelta(days=6)
                )
            )
            == 5
        )


async def test_removing_nothing_says_so(app_factory: AppFactory) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        calendar(app).go_to(FREE_MONDAY)
        await pilot.pause()
        await pilot.press("x")
        await pilot.pause()
        assert "Nothing booked" in status_text(app)


# ---- the surface ----


async def test_seed_is_drawn(app_factory: AppFactory) -> None:
    """Bookings from the database reach the grid."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        ledgers = calendar(app).ledgers
        assert ledgers[date(2026, 6, 12)].absences, "the seed's TOIL day"
        assert ledgers[date(2026, 5, 25)].is_holiday, "the spring bank holiday"


async def test_every_panel_is_jumpable(app_factory: AppFactory) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        leave = showing(app, LeaveScreen)
        for widget_id in leave.jump_targets():
            assert leave.query(f"#{widget_id}"), f"{widget_id} is not mounted"


async def test_wallet_names_the_dashboard_year(
    app_factory: AppFactory,
) -> None:
    """Three panels name one span the same way; `Apr 26` is not the 6th."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        wallet = app.screen.query_one("#leave-wallet")
        assert str(wallet.border_subtitle) == "6 Apr 26–5 Apr 27"


async def test_rail_gives_way_when_there_is_no_room(
    app_factory: AppFactory,
) -> None:
    """At 36 cells the rail leaves the calendar four days of a week."""
    app = app_factory()
    async with app.run_test(size=(84, 28)) as pilot:
        await open_leave(pilot)
        assert app.screen.query_one("#leave-wallet").display is False
        assert app.screen.query_one("#leave-wallet-line").display is True


async def test_grid_never_outgrows_its_panel(app_factory: AppFactory) -> None:
    """A week has to keep reading as a row at every width."""
    for size in ((120, 36), (84, 28), (64, 22)):
        app = app_factory()
        async with app.run_test(size=size) as pilot:
            await open_leave(pilot)
            grid = calendar(app)
            assert grid.grid_width <= max(grid.content_size.width, grid.size.width)


async def test_booking_made_elsewhere_reaches_the_grid(
    app_factory: AppFactory,
) -> None:
    """`refresh_open_screens` invalidates once and tells every open screen.

    A booking written by the command palette while the leave year is on screen
    reaches the grid without the user leaving and coming back.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        assert not calendar(app).ledgers[FREE_MONDAY].absences
        before = str(calendar(app).border_subtitle)

        app.services.absence.book(FREE_MONDAY, AbsenceType.ANNUAL)
        app.refresh_open_screens()
        await pilot.pause()

        assert calendar(app).ledgers[FREE_MONDAY].absences
        assert str(calendar(app).border_subtitle) != before, (
            "the year's running total should have moved with it"
        )


async def test_resize_before_mount_redraws_nothing(
    app_factory: AppFactory,
) -> None:
    """The width class is applied on every resize; the selection line is not.

    A `Resize` can reach the screen before `compose` has put the rail on it,
    and `_draw_selection` reads three widgets that do not exist yet.
    """
    app = app_factory()
    async with app.run_test(size=(64, 22)):
        screen = LeaveScreen(app.services)
        assert not screen.is_mounted
        assert not screen.query("#leave-selection-booked"), "nothing to draw on yet"

        screen.on_resize()

        assert screen.has_class("-narrow"), "the width still gets recorded"


# ---- the preview, without a screen to put it on ----


def _plan(days: tuple[PlannedDay, ...], **kwargs: object) -> AbsencePlan:
    """A plan built by hand, so the wording can be tested without a database."""
    defaults: dict[str, object] = {
        "absence_type": AbsenceType.ANNUAL,
        "portion": Portion.FULL,
        "note": None,
        "start": days[0].date,
        "end": days[-1].date,
        "days": days,
    }
    return AbsencePlan(**{**defaults, **kwargs})  # type: ignore[arg-type]


def _day(when: date, verdict: Verdict) -> PlannedDay:
    return PlannedDay(date=when, verdict=verdict, reason="")


def test_preview_tells_weekend_from_bank_holiday() -> None:
    """Both are passed over, and only one is a day that would have been spent."""
    monday = date(2026, 8, 24)
    shown = preview(
        _plan(
            (
                _day(monday, Verdict.BOOK),
                _day(monday + timedelta(days=1), Verdict.BANK_HOLIDAY),
                _day(monday + timedelta(days=5), Verdict.NON_WORKING),
                _day(monday + timedelta(days=6), Verdict.NON_WORKING),
            )
        )
    )

    assert "2 non-working days" in shown
    assert "1 bank holiday" in shown


def test_preview_counts_one_of_each_singular() -> None:
    monday = date(2026, 8, 24)
    shown = preview(
        _plan(
            (
                _day(monday, Verdict.BOOK),
                _day(monday + timedelta(days=1), Verdict.BANK_HOLIDAY),
                _day(monday + timedelta(days=5), Verdict.NON_WORKING),
            )
        )
    )

    assert "1 non-working day" in shown
    assert "1 bank holiday" in shown
    assert "1 day of 3" in shown, "and the headline agrees"


def test_preview_names_a_lone_bank_holiday() -> None:
    """The two counts are written separately, so one can appear without the other."""
    monday = date(2026, 8, 24)
    shown = preview(
        _plan(
            (
                _day(monday, Verdict.BOOK),
                _day(monday + timedelta(days=1), Verdict.BANK_HOLIDAY),
                _day(monday + timedelta(days=2), Verdict.BOOK),
            )
        )
    )

    assert "1 bank holiday" in shown
    assert "non-working" not in shown, "there was no weekend in the span"


def test_preview_keeps_cross_year_allowances_apart() -> None:
    monday = date(2026, 12, 28)
    shown = preview(
        _plan(
            (
                _day(monday, Verdict.BOOK),
                _day(monday + timedelta(days=7), Verdict.BOOK),
            ),
            annual_balances=(
                AnnualBalance(2026, 1.0, 0.0),
                AnnualBalance(2027, 2.0, 1.0),
            ),
        )
    )

    assert "Annual leave 2026: 1 → 0 left" in shown
    assert "Annual leave 2027: 2 → 1 left" in shown


def test_preview_carries_its_warning() -> None:
    """The warning belongs in the question, not only in the receipt after it."""
    monday = date(2026, 8, 24)
    days = tuple(_day(monday + timedelta(days=n), Verdict.BOOK) for n in range(3))
    plan = _plan(days, absence_type=AbsenceType.FLEXI, toil_available=0.0)

    assert plan.warning is not None
    assert plan.warning in preview(plan)


def test_overdrawing_by_one_day_reads_as_one_day() -> None:
    monday = date(2026, 8, 24)
    days = tuple(_day(monday + timedelta(days=n), Verdict.BOOK) for n in range(3))

    one = _plan(days[:1], absence_type=AbsenceType.FLEXI, toil_available=0.0)
    three = _plan(days, absence_type=AbsenceType.FLEXI, toil_available=0.0)

    assert one.warning == "This takes the flexi balance 1 day into deficit"
    assert three.warning == "This takes the flexi balance 3 days into deficit"


# ---- the modal, on a span ----


async def test_modal_shows_the_span_it_would_book(
    app_factory: AppFactory,
) -> None:
    """`e` on a span shows both ends before anything is written."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        calendar(app).go_to(FREE_MONDAY)
        await pilot.pause()
        for _ in range(4):
            await pilot.press("shift+right")
        await pilot.pause()

        await pilot.press("e")
        await pilot.pause()
        modal = showing(app, AbsenceModal)
        assert modal.query_one("#absence-until", Input).value == (
            (FREE_MONDAY + timedelta(days=4)).isoformat()
        ), "the last day is on screen, editable, before anything is written"

        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal), "a span asks first, as `A` does"
        assert (
            app.services.absence.in_range(FREE_MONDAY, FREE_MONDAY + timedelta(days=4))
            == []
        ), "and writes nothing until it is answered"

        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        booked = app.services.absence.in_range(
            FREE_MONDAY, FREE_MONDAY + timedelta(days=4)
        )
        assert len(booked) == 5


async def test_modal_on_one_day_asks_for_one_date(
    app_factory: AppFactory,
) -> None:
    """A second field for a span of one is a field with nothing to say."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        calendar(app).go_to(FREE_MONDAY)
        await pilot.pause()

        await pilot.press("e")
        await pilot.pause()
        assert not app.screen.query("#absence-until")

        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert len(app.services.absence.for_date(FREE_MONDAY)) == 1


async def test_backwards_span_is_refused(
    app_factory: AppFactory,
) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        calendar(app).go_to(FREE_MONDAY)
        await pilot.pause()
        for _ in range(4):
            await pilot.press("shift+right")
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()

        until = showing(app, AbsenceModal).query_one("#absence-until", Input)
        until.value = (FREE_MONDAY - timedelta(days=3)).isoformat()
        await pilot.press("enter")
        await pilot.pause()

        showing(app, AbsenceModal)  # the modal stays put
        assert "before the first" in screen_text(app)
        assert app.services.absence.in_range(FREE_MONDAY, FREE_MONDAY) == []


async def test_cursor_leaving_the_year_reloads_the_grid(
    app_factory: AppFactory,
) -> None:
    """The calendar holds one leave year; the cursor is not confined to it.

    Reached by moving the selection, not by the go-to modal: the calendar
    reports where it went and the screen notices it is outside what it drew.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        before = showing(app, LeaveScreen).period
        assert not before.contains(date(2028, 6, 14))

        calendar(app).go_to(date(2028, 6, 14))
        await pilot.pause()
        await pilot.pause()

        leave = showing(app, LeaveScreen)
        assert leave.period != before, "the shown year followed the cursor"
        assert leave.period.contains(date(2028, 6, 14))
        assert calendar(app).selection.head == date(2028, 6, 14)


async def test_settings_move_the_leave_year_under_the_planner(
    app_factory: AppFactory,
) -> None:
    """The planner is measured against a leave year the settings own."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        assert showing(app, LeaveScreen).period.start == date(2026, 4, 6)

        settings = app.services.settings
        current = settings.resolved()
        settings.save_settings(
            SettingsUpdate(
                leave_year_start=(1, 1),
                working_days=current.working_days,
                division=current.division,
                auto_close=current.auto_close,
            )
        )
        app.refresh_open_screens(Scope.SETTINGS)
        await pilot.pause()

        assert showing(app, LeaveScreen).period.start == date(2026, 1, 1)

        moved = showing(app, LeaveScreen).period
        app.refresh_open_screens(Scope.ABSENCE)
        await pilot.pause()

        assert showing(app, LeaveScreen).period == moved, (
            "a booking redraws the year; it does not re-read where it starts"
        )


# ---- the cursor, the grid and the fold ----


async def test_planner_opens_the_cursor_on_its_year(
    app_factory: AppFactory,
) -> None:
    """The cursor lands on the year that is drawn, whatever was browsed."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("y")
        await pilot.pause()
        await pilot.press("left_square_bracket")
        await pilot.pause()

        await open_leave(pilot)
        screen = showing(app, LeaveScreen)
        head = calendar(app).selection.head
        assert screen.period.contains(head)
        assert calendar(app).row_of(head) is not None, "the cursor is on the grid"
        assert head == screen.period.anchor


async def test_cursor_is_on_screen_in_a_january_year(
    app_factory: AppFactory,
) -> None:
    """A leave year starting five months ago opens scrolled to January.

    The grid is taller than the panel, so the cursor has to be scrolled to.
    """
    app = app_factory()
    async with app.run_test(size=(120, 30)) as pilot:
        settings = app.services.settings
        current = settings.resolved()
        settings.save_settings(
            SettingsUpdate(
                leave_year_start=(1, 1),
                working_days=current.working_days,
                division=current.division,
                auto_close=current.auto_close,
            )
        )

        await open_leave(pilot)
        await pilot.pause()
        grid = calendar(app)
        row = grid.row_of(TODAY)
        assert row is not None
        top = int(grid.scroll_offset.y)
        assert top <= row < top + grid.size.height, (
            f"today is on row {row}, showing {top}..{top + grid.size.height}"
        )


# ---- what the selection costs ----


async def test_bank_holiday_is_not_a_working_day(
    app_factory: AppFactory,
) -> None:
    """The sidebar and the booking that follows count the same days."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        calendar(app).go_to(date(2026, 8, 31))  # the Summer bank holiday
        await pilot.pause()
        for _ in range(4):
            await pilot.press("shift+right")
        await pilot.pause()

        detail = showing(app, LeaveScreen).query_one("#leave-selection-detail", Static)
        assert "4 working days" in str(detail.render())

        await pilot.press("A")
        await pilot.pause()
        showing(app, ConfirmModal)
        assert "4 days of 5" in screen_text(app)


async def test_selection_past_the_drawn_year_counts(
    app_factory: AppFactory,
) -> None:
    """The grid holds one leave year and the selection is not confined to it.

    Dragged over the boundary the calendar reloads, so the anchor is a day it
    holds no ledger for and the weekday pattern answers for it.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        calendar(app).go_to(date(2027, 4, 5))  # a Monday, the last day of the year
        await pilot.pause()
        await pilot.press("shift+right")
        await pilot.pause()
        await pilot.pause()

        screen = showing(app, LeaveScreen)
        assert screen.period.start == date(2027, 4, 6), "the grid followed the cursor"
        detail = screen.query_one("#leave-selection-detail", Static)
        assert "2 working days" in str(detail.render())


# ---- the keyboard on a dialog ----


async def test_enter_on_the_cancel_button_keeps_the_bookings(
    app_factory: AppFactory,
) -> None:
    """The enter binding stands down while Cancel has focus."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        calendar(app).go_to(FREE_MONDAY)
        await pilot.pause()
        for _ in range(6):
            await pilot.press("shift+right")
        await pilot.pause()
        await pilot.press("A")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        await pilot.press("x")
        await pilot.pause()
        removal = showing(app, ConfirmModal)
        removal.query_one("#modal-cancel", Button).focus()
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        showing(app, LeaveScreen)
        kept = app.services.absence.in_range(
            FREE_MONDAY, FREE_MONDAY + timedelta(days=6)
        )
        assert len(kept) == 5, "cancel is the one button that has to be trustworthy"


# ---- what the modal says is left ----


async def test_allowance_hint_follows_the_booked_year(
    app_factory: AppFactory,
) -> None:
    """Next April draws on next year's allowance."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        settings = app.services.settings
        settings.save_entitlement(2027, 30.0)

        await open_leave(pilot)
        calendar(app).go_to(date(2027, 4, 12))
        await pilot.pause()
        await pilot.pause()

        await pilot.press("e")
        await pilot.pause()
        modal = showing(app, AbsenceModal)
        assert modal._remaining == 30.0
        assert "30 days annual leave left" in modal._allowance_hint()
