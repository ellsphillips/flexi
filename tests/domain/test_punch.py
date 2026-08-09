from datetime import date, datetime, time, timedelta

import pytest

from flexi import wallclock
from flexi.constants import AbsenceType, DayKind, Portion
from flexi.domain.ledger import AbsenceSlice, DayLedger, Segment
from flexi.domain.punch import Cell, Window, bucket_minutes, cell_count, strip

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
    """A local reading, carrying its offset. The domain refuses naive moments."""
    return wallclock.local(datetime.combine(DAY, time(hour, minute)))


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


# -- resolution ------------------------------------------------------------


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
    """It coarsens to fit rather than truncating the window."""
    window = Window()
    assert bucket_minutes(window, width) == minutes
    assert cell_count(window, width) == cells


def test_it_never_draws_wider_than_it_was_given() -> None:
    """It fits any width from 12 upward."""
    window = Window()
    for width in range(12, 200):
        assert len(strip(ledger(), width, window)) <= width


def test_below_twelve_columns_it_summarises() -> None:
    """It falls back to morning, afternoon and evening rather than lying."""
    assert len(strip(ledger(), 8)) == 3
    assert len(strip(ledger(), 1)) == 3


# -- states ----------------------------------------------------------------


def test_an_empty_working_day_is_all_window() -> None:
    """It draws nothing but the window when nobody clocked in."""
    assert render(strip(ledger(), 12)) == "------------"


def test_a_bank_holiday_covers_the_whole_strip() -> None:
    """It says bank holiday and nothing else."""
    assert render(strip(ledger(holiday="Spring bank holiday"), 12)) == "============"


def test_a_full_day_absence_covers_the_whole_strip() -> None:
    """It fills every cell an absence covers."""
    booked = (AbsenceSlice(1, AbsenceType.ANNUAL, Portion.FULL),)
    assert (
        render(strip(ledger(absences=booked, expected=timedelta()), 12))
        == "############"
    )


def test_a_morning_absence_covers_only_the_morning() -> None:
    """It splits the day at midday."""
    booked = (AbsenceSlice(1, AbsenceType.SICK, Portion.AM),)
    #  07:00 .. 19:00 in 12 one-hour cells; midday is the sixth boundary
    assert render(strip(ledger(absences=booked), 12)) == "#####-------"


def test_an_afternoon_absence_covers_only_the_afternoon() -> None:
    """It splits the day at midday, the other way."""
    booked = (AbsenceSlice(1, AbsenceType.FLEXI, Portion.PM),)
    assert render(strip(ledger(absences=booked), 12)) == "-----#######"


def test_work_overrides_a_booked_half_day() -> None:
    """It shows the work that actually happened over the absence booked around it.

    A half day expects half the contract, so the go-home tick lands at 12:42.
    """
    booked = (AbsenceSlice(1, AbsenceType.ANNUAL, Portion.AM),)
    worked = (Segment(1, at(9), at(11)),)
    strip_ = strip(
        ledger(absences=booked, segments=worked, expected=CONTRACTED / 2), 12
    )
    assert render(strip_) == "##XX#|------"


def test_a_break_between_two_sessions() -> None:
    """It marks the gap between sessions, but not the day either side of them.

    An hour of break pushes the go-home tick out to 17:24.
    """
    worked = (Segment(1, at(9), at(12)), Segment(2, at(13), at(17)))
    assert render(strip(ledger(segments=worked), 12)) == "--XXX.XXXX|-"


def test_time_before_arriving_is_not_a_break() -> None:
    """It only calls a gap a break when it sits between two sessions."""
    worked = (Segment(1, at(9), at(11)),)
    assert Cell.BREAK not in strip(ledger(segments=worked), 12)


def test_an_open_session_marks_the_live_edge() -> None:
    """It highlights the cell the running session is currently in."""
    worked = (Segment(1, at(9), None),)
    cells = strip(ledger(segments=worked), 12, now=at(14, 30))
    assert render(cells) == "--XXXXX>-|--"


def test_a_short_session_lights_a_whole_cell() -> None:
    """It shows presence rather than proportion, so nothing vanishes."""
    worked = (Segment(1, at(9, 5), at(9, 10)),)
    assert render(strip(ledger(segments=worked), 12)) == "--X------|--"


def test_the_target_tick_marks_when_hours_are_met() -> None:
    """It says when you can go home, allowing for the breaks you took."""
    worked = (Segment(1, at(9), at(12)), Segment(2, at(13), at(15)))
    # 09:00 + 7h24 contracted + 1h break = 17:24, inside the 17:00 cell
    cells = strip(ledger(segments=worked), 12)
    assert cells[10] is Cell.TARGET


def test_no_target_tick_before_arriving() -> None:
    """It has no answer to when you can leave before you have arrived."""
    assert Cell.TARGET not in strip(ledger(), 12)


def test_no_target_tick_on_a_day_that_expects_nothing() -> None:
    """It draws no go-home tick on a day with no hours to meet."""
    worked = (Segment(1, at(9), at(11)),)
    assert Cell.TARGET not in strip(ledger(segments=worked, expected=timedelta()), 12)


def test_a_window_can_be_parsed_and_measured() -> None:
    """It reads the configured day window."""
    window = Window.parse("08:00", "18:30")
    assert window.minutes == 630
