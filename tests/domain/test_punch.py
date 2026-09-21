from datetime import date, datetime, time, timedelta

import pytest

from flexi import wallclock
from flexi.constants import AbsenceType, DayKind, Portion
from flexi.domain.ledger import AbsenceSlice, DayLedger, Segment
from flexi.domain.punch import (
    Cell,
    Window,
    bucket_minutes,
    cell_count,
    edges,
    strip,
)

DAY = date(2026, 6, 11)
CONTRACTED = timedelta(hours=7, minutes=24)

GLYPHS = {
    Cell.OFF: "-",
    Cell.BREAK: ".",
    Cell.TARGET: "|",
    Cell.ABSENCE: "#",
    Cell.HOLIDAY: "=",
    Cell.ON: "X",
    Cell.LIVE: ">",
}


def render(cells: tuple[Cell, ...]) -> str:
    return "".join(GLYPHS[cell] for cell in cells)


def at(hour: int, minute: int = 0) -> datetime:
    """A local reading, carrying its offset: the domain refuses naive moments."""
    return wallclock.local(datetime.combine(DAY, time(hour, minute)))


EVENING = at(23, 59)
"""When these strips are drawn, unless a test is about the moment itself.

`strip` takes `now`, so every test says which moment it means. The evening
draws a closed day the same however often it is redrawn."""


def ledger(
    *,
    segments: tuple[Segment, ...] = (),
    absences: tuple[AbsenceSlice, ...] = (),
    holiday: str | None = None,
    expected: timedelta = CONTRACTED,
    kind: DayKind = DayKind.WORKING,
) -> DayLedger:
    return DayLedger(
        date=DAY,
        kind=kind,
        is_working_day=True,
        contracted=CONTRACTED,
        worked=timedelta(),
        expected=expected,
        holiday_title=holiday,
        absences=absences,
        segments=segments,
    )


# ---- resolution ----


@pytest.mark.parametrize(
    ("width", "minutes", "cells"),
    [
        (200, 5, 144),
        (80, 10, 72),
        (48, 15, 48),
        (36, 20, 36),
        (24, 30, 24),
        (12, 60, 12),
    ],
)
def test_it_takes_the_finest_bucket_that_fits(
    width: int, minutes: int, cells: int
) -> None:
    window = Window()
    assert bucket_minutes(window, width) == minutes
    assert cell_count(window, width) == cells


def test_it_never_draws_wider_than_it_was_given() -> None:
    window = Window()
    for width in range(12, 200):
        assert len(strip(ledger(), width, window, now=EVENING)) <= width


def test_bucket_sizes_stop_at_an_hour() -> None:
    """``BUCKET_SIZES`` runs from five minutes to an hour and stops there."""
    six_to_ten = Window(time(6, 0), time(22, 0))
    assert bucket_minutes(six_to_ten, 12) == 60


def test_below_twelve_columns_it_summarises() -> None:
    """It falls back to morning, afternoon and evening."""
    assert len(strip(ledger(), 8, now=EVENING)) == 3
    assert len(strip(ledger(), 1, now=EVENING)) == 3


# ---- states ----


def test_empty_working_day_is_all_window() -> None:
    assert render(strip(ledger(), 12, now=EVENING)) == "------------"


def test_bank_holiday_covers_the_whole_strip() -> None:
    assert (
        render(strip(ledger(holiday="Spring bank holiday"), 12, now=EVENING))
        == "============"
    )


def test_full_day_absence_covers_the_whole_strip() -> None:
    booked = (AbsenceSlice(1, AbsenceType.ANNUAL, Portion.FULL),)
    assert (
        render(strip(ledger(absences=booked, expected=timedelta()), 12, now=EVENING))
        == "############"
    )


def test_morning_absence_covers_only_the_morning() -> None:
    booked = (AbsenceSlice(1, AbsenceType.SICK, Portion.AM),)
    #  07:00 .. 19:00 in 12 one-hour cells; midday is the sixth boundary
    assert render(strip(ledger(absences=booked), 12, now=EVENING)) == "#####-------"


def test_afternoon_absence_covers_the_afternoon() -> None:
    booked = (AbsenceSlice(1, AbsenceType.FLEXI, Portion.PM),)
    assert render(strip(ledger(absences=booked), 12, now=EVENING)) == "-----#######"


def test_work_overrides_a_booked_half_day() -> None:
    """A half day expects half the contract, so the go-home tick lands at 12:42."""
    booked = (AbsenceSlice(1, AbsenceType.ANNUAL, Portion.AM),)
    worked = (Segment(1, at(9), at(11)),)
    strip_ = strip(
        ledger(absences=booked, segments=worked, expected=CONTRACTED / 2),
        12,
        now=EVENING,
    )
    assert render(strip_) == "##XX#|------"


def test_break_between_two_sessions() -> None:
    """An hour of break pushes the go-home tick out to 17:24."""
    worked = (Segment(1, at(9), at(12)), Segment(2, at(13), at(17)))
    assert render(strip(ledger(segments=worked), 12, now=EVENING)) == "--XXX.XXXX|-"


def test_time_before_arriving_is_not_a_break() -> None:
    worked = (Segment(1, at(9), at(11)),)
    assert Cell.BREAK not in strip(ledger(segments=worked), 12, now=EVENING)


def test_open_session_marks_the_live_edge() -> None:
    worked = (Segment(1, at(9), None),)
    cells = strip(ledger(segments=worked), 12, now=at(14, 30))
    assert render(cells) == "--XXXXX>-|--"


def test_session_past_the_window_has_no_live_cell() -> None:
    """Marking the last cell instead would put the live edge at 19:00."""
    worked = (Segment(1, at(18), None),)
    cells = strip(ledger(segments=worked), 12, now=at(20, 30))

    assert render(cells) == "-----------X", "the session still shows up to the edge"
    assert Cell.LIVE not in cells


def test_session_outside_the_window_is_not_drawn() -> None:
    """The window is a setting, and an evening shift needs one that holds it."""
    evening = (Segment(1, at(20), at(23, 30)),)

    assert render(strip(ledger(segments=evening), 24, now=EVENING)) == "-" * 24
    assert Cell.ON in strip(
        ledger(segments=evening),
        24,
        Window(time(19, 0), time(23, 59)),
        now=EVENING,
    ), "the same day, drawn against a window that holds it"


def test_short_session_lights_a_whole_cell() -> None:
    """A cell shows presence, not proportion, so nothing vanishes."""
    worked = (Segment(1, at(9, 5), at(9, 10)),)
    assert render(strip(ledger(segments=worked), 12, now=EVENING)) == "--X------|--"


def test_target_tick_marks_when_hours_are_met() -> None:
    worked = (Segment(1, at(9), at(12)), Segment(2, at(13), at(15)))
    # 09:00 + 7h24 contracted + 1h break = 17:24, inside the 17:00 cell
    cells = strip(ledger(segments=worked), 12, now=EVENING)
    assert cells[10] is Cell.TARGET


def test_target_tick_never_paints_over_work() -> None:
    """The tick is a prediction and the session a record, so the record keeps it."""
    worked = (Segment(1, at(9), at(17)),)  # contracted hours met at 16:24
    cells = strip(ledger(segments=worked), 12, now=EVENING)

    assert render(cells) == "--XXXXXXXX--"
    assert Cell.TARGET not in cells


def test_no_target_tick_before_arriving() -> None:
    assert Cell.TARGET not in strip(ledger(), 12, now=EVENING)


def test_no_target_tick_on_a_day_that_expects_nothing() -> None:
    worked = (Segment(1, at(9), at(11)),)
    nothing_expected = ledger(segments=worked, expected=timedelta())
    assert Cell.TARGET not in strip(nothing_expected, 12, now=EVENING)


def test_no_target_tick_when_the_hours_run_past_the_window() -> None:
    """Clocking in at four ends contracted hours at 23:24, past the 19:00 window."""
    late = (Segment(1, at(16), at(18)),)
    assert Cell.TARGET not in strip(ledger(segments=late), 12, now=EVENING)


def test_window_can_be_parsed_and_measured() -> None:
    window = Window.parse("08:00", "18:30")
    assert window.minutes == 630


# ---- the grid across a transition ----

SPRING_FORWARD = date(2026, 3, 29)
"""The Sunday Europe/London loses an hour, at 01:00 GMT."""

AUTUMN_BACK = date(2026, 10, 25)
"""The Sunday Europe/London gains one, at 02:00 BST."""

NIGHT = Window(time(0, 0), time(6, 0))
"""A window wide enough to contain a transition.

The default 07:00-19:00 is not: no zone moves its clocks inside working hours,
so every other test here takes the evenly spaced fast path.
"""


@pytest.mark.usefixtures("in_london")
@pytest.mark.parametrize("day", [SPRING_FORWARD, AUTUMN_BACK])
@pytest.mark.parametrize("count", [12, 44])
def test_grid_stays_a_wall_grid_across_a_transition(day: date, count: int) -> None:
    """Each bound is localised on its own when the offset moves under the window.

    `edges` generates the interior arithmetically while the two ends share an
    offset. On the October Sunday an evenly spaced grid would put the hour that
    happens twice in one cell.
    """
    bounds = edges(day, count, NIGHT)

    assert len(bounds) == count + 1
    assert [b.time() for b in bounds] == [
        (
            datetime.combine(day, NIGHT.start)
            + timedelta(minutes=index * NIGHT.minutes / count)
        ).time()
        for index in range(count + 1)
    ], "the wall readings are evenly spaced whatever the offsets under them are"
    offsets = {bound.utcoffset() for bound in bounds}
    assert len(offsets) == 2, "the transition is inside the window, so both apply"


@pytest.mark.usefixtures("in_london")
def test_clear_window_is_spaced_the_same_way() -> None:
    """Generating the interior is only sound if it matches localising each bound."""
    bounds = edges(AUTUMN_BACK, 44, Window())

    assert len({bound.utcoffset() for bound in bounds}) == 1
    assert bounds == [
        wallclock.local(
            Window().moment(
                datetime.combine(AUTUMN_BACK, time.min), index * Window().minutes / 44
            )
        )
        for index in range(45)
    ]
