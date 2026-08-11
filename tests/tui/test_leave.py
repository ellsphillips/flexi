"""The leave screen: a year you move a cursor over and book on."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from textual.pilot import Pilot
from textual.widgets import Input

from flexi.app import FlexiApp
from flexi.components.common import Gauge
from flexi.components.yearcalendar import YearCalendar
from flexi.constants import AbsenceType, Portion, Verdict
from flexi.screens.leave import LeaveScreen, preview
from flexi.screens.modals import AbsenceModal, ConfirmModal
from flexi.services.absence import AbsencePlan, PlannedDay
from tests.tui.conftest import WIDE, AppFactory, screen_text, showing, status_text

TODAY = date(2026, 6, 11)  # a Thursday
FREE_MONDAY = date(2026, 6, 22)  # nothing booked on it in the seed


def calendar(app: FlexiApp) -> YearCalendar:
    return app.screen.query_one(YearCalendar)


async def open_leave(pilot: Pilot[None]) -> None:
    await pilot.press("f2")
    await pilot.pause()


# -- getting there ---------------------------------------------------------


async def test_f2_opens_the_leave_year(app_factory: AppFactory) -> None:
    """It opens on the leave year, with the cursor on today."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        assert showing(app, LeaveScreen).period.start == date(2026, 4, 6)
        assert calendar(app).selection.head == TODAY


async def test_escape_leaves(app_factory: AppFactory) -> None:
    """With nothing selected, escape is the way out.

    The calendar binds it too, to collapse a selection — a focused widget is
    asked first, so it stands the binding down when there is nothing to
    collapse rather than trapping somebody on the screen.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, LeaveScreen)


async def test_the_whole_year_is_laid_out(app_factory: AppFactory) -> None:
    """Thirteen months, because a leave year starting on the 6th touches both ends."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        assert len(calendar(app).blocks) == 13


# -- moving ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "days"),
    [("right", 1), ("left", -1), ("down", 7), ("up", -7), ("l", 1), ("k", -7)],
)
async def test_the_cursor_moves(app_factory: AppFactory, key: str, days: int) -> None:
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


async def test_a_month_step_clamps_to_a_shorter_month(app_factory: AppFactory) -> None:
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


# -- booking ---------------------------------------------------------------


async def test_one_key_books_a_day(app_factory: AppFactory) -> None:
    """No modal. The cursor is the subject.

    A dialog in front of every single-day booking would cost more than it saves,
    and the app books a day the way it clocks in: one key, immediately, visibly.
    `x` takes it back, which is cheaper than being asked.
    """
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
    """Five working days, and the weekend is not mentioned because it is not news.

    A span is previewed before it is written. The screen used to call
    `book_range`, which plans and commits in one breath, so it could book
    eleven days, refuse the twelfth and tell you afterwards.
    """
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


async def test_the_preview_says_what_it_will_and_will_not_do(
    app_factory: AppFactory,
) -> None:
    """A preview that only says "5 days" is a receipt written in advance."""
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
    """Half days are rare, so they cost one keystroke on the rare path."""
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
    """It needs a note, and a note needs somewhere to be typed."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        await pilot.press("O")
        await pilot.pause()
        assert isinstance(app.screen, AbsenceModal)


async def test_the_wallet_moves_with_the_booking(app_factory: AppFactory) -> None:
    """The question behind every booking is whether you can afford it."""
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


# -- removing --------------------------------------------------------------


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
    """A key that can wipe a fortnight without a word is pressed once, ever."""
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


async def test_removing_nothing_says_so(app_factory: AppFactory) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await open_leave(pilot)
        calendar(app).go_to(FREE_MONDAY)
        await pilot.pause()
        await pilot.press("x")
        await pilot.pause()
        assert "Nothing booked" in status_text(app)


# -- the surface -----------------------------------------------------------


async def test_the_seed_is_drawn(app_factory: AppFactory) -> None:
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


async def test_the_rail_gives_way_when_there_is_no_room(
    app_factory: AppFactory,
) -> None:
    """At 36 cells the rail leaves the calendar four days of a week."""
    app = app_factory()
    async with app.run_test(size=(84, 28)) as pilot:
        await open_leave(pilot)
        assert app.screen.query_one("#leave-wallet").display is False
        assert app.screen.query_one("#leave-wallet-line").display is True


async def test_the_grid_never_outgrows_its_panel(app_factory: AppFactory) -> None:
    """A week has to keep reading as a row at every width."""
    for size in ((120, 36), (84, 28), (64, 22)):
        app = app_factory()
        async with app.run_test(size=size) as pilot:
            await open_leave(pilot)
            grid = calendar(app)
            assert grid.grid_width <= max(grid.content_size.width, grid.size.width)


# -- the preview, without a screen to put it on ----------------------------


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


def test_the_preview_tells_a_weekend_from_a_bank_holiday() -> None:
    """Both are passed over; only one of them is a day somebody would have spent.

    Lumping the two together read as "3 non-working days", which quietly
    presented a bank holiday as though it were a Saturday.
    """
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


def test_the_preview_counts_one_of_each_in_the_singular() -> None:
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


def test_overdrawing_the_balance_by_one_day_reads_as_one_day() -> None:
    """`3 day into deficit` was the sentence being assembled in two places."""
    monday = date(2026, 8, 24)
    days = tuple(_day(monday + timedelta(days=n), Verdict.BOOK) for n in range(3))

    one = _plan(days[:1], absence_type=AbsenceType.FLEXI, toil_available=0.0)
    three = _plan(days, absence_type=AbsenceType.FLEXI, toil_available=0.0)

    assert one.warning == "This takes the flexi balance 1 day into deficit"
    assert three.warning == "This takes the flexi balance 3 days into deficit"


# -- the modal, on a span --------------------------------------------------


async def test_the_modal_shows_the_span_it_would_book(
    app_factory: AppFactory,
) -> None:
    """`e` on a fortnight used to show one date and write fourteen days."""
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
        booked = app.services.absence.in_range(
            FREE_MONDAY, FREE_MONDAY + timedelta(days=4)
        )
        assert len(booked) == 5


async def test_the_modal_on_one_day_asks_for_one_date(
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


async def test_a_backwards_span_is_refused_before_it_is_written(
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
