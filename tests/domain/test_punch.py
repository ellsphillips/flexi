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


EVENING = at(23, 59)
"""When these strips are drawn, unless a test is about the moment itself.

`strip` takes `now` rather than guessing at it, so a test has to say. The
evening is the reading that draws a closed day the same however often it is
redrawn -- which is what the old `None` default was trying, and failing, to
mean."""


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
        assert len(strip(ledger(), width, window, now=EVENING)) <= width


def test_the_ladder_of_bucket_sizes_stops_at_an_hour() -> None:
    """It coarsens to the last rung rather than inventing one below it.

    ``BUCKET_SIZES`` is the set of divisions a reader can count in their head —
    five minutes up to an hour. A sixteen-hour window in twelve columns fits
    none of them, and the answer is the hour, not a ninety-minute cell nobody
    could read off the strip.
    """
    six_to_ten = Window(time(6, 0), time(22, 0))
    assert bucket_minutes(six_to_ten, 12) == 60


def test_below_twelve_columns_it_summarises() -> None:
    """It falls back to morning, afternoon and evening rather than lying."""
    assert len(strip(ledger(), 8, now=EVENING)) == 3
    assert len(strip(ledger(), 1, now=EVENING)) == 3


# -- states ----------------------------------------------------------------


def test_an_empty_working_day_is_all_window() -> None:
    """It draws nothing but the window when nobody clocked in."""
    assert render(strip(ledger(), 12, now=EVENING)) == "------------"


def test_a_bank_holiday_covers_the_whole_strip() -> None:
    """It says bank holiday and nothing else."""
    assert (
        render(strip(ledger(holiday="Spring bank holiday"), 12, now=EVENING))
        == "============"
    )


def test_a_full_day_absence_covers_the_whole_strip() -> None:
    """It fills every cell an absence covers."""
    booked = (AbsenceSlice(1, AbsenceType.ANNUAL, Portion.FULL),)
    assert (
        render(strip(ledger(absences=booked, expected=timedelta()), 12, now=EVENING))
        == "############"
    )


def test_a_morning_absence_covers_only_the_morning() -> None:
    """It splits the day at midday."""
    booked = (AbsenceSlice(1, AbsenceType.SICK, Portion.AM),)
    #  07:00 .. 19:00 in 12 one-hour cells; midday is the sixth boundary
    assert render(strip(ledger(absences=booked), 12, now=EVENING)) == "#####-------"


def test_an_afternoon_absence_covers_only_the_afternoon() -> None:
    """It splits the day at midday, the other way."""
    booked = (AbsenceSlice(1, AbsenceType.FLEXI, Portion.PM),)
    assert render(strip(ledger(absences=booked), 12, now=EVENING)) == "-----#######"


def test_work_overrides_a_booked_half_day() -> None:
    """It shows the work that actually happened over the absence booked around it.

    A half day expects half the contract, so the go-home tick lands at 12:42.
    """
    booked = (AbsenceSlice(1, AbsenceType.ANNUAL, Portion.AM),)
    worked = (Segment(1, at(9), at(11)),)
    strip_ = strip(
        ledger(absences=booked, segments=worked, expected=CONTRACTED / 2),
        12,
        now=EVENING,
    )
    assert render(strip_) == "##XX#|------"


def test_a_break_between_two_sessions() -> None:
    """It marks the gap between sessions, but not the day either side of them.

    An hour of break pushes the go-home tick out to 17:24.
    """
    worked = (Segment(1, at(9), at(12)), Segment(2, at(13), at(17)))
    assert render(strip(ledger(segments=worked), 12, now=EVENING)) == "--XXX.XXXX|-"


def test_time_before_arriving_is_not_a_break() -> None:
    """It only calls a gap a break when it sits between two sessions."""
    worked = (Segment(1, at(9), at(11)),)
    assert Cell.BREAK not in strip(ledger(segments=worked), 12, now=EVENING)


def test_an_open_session_marks_the_live_edge() -> None:
    """It highlights the cell the running session is currently in."""
    worked = (Segment(1, at(9), None),)
    cells = strip(ledger(segments=worked), 12, now=at(14, 30))
    assert render(cells) == "--XXXXX>-|--"


def test_a_session_running_past_the_window_lights_no_cell_as_live() -> None:
    """Working past seven puts the leading edge off the end of the strip.

    The window is the span the user chose to draw, and half past eight is not in
    it. Marking the last cell instead would put the live edge at 19:00 and read
    as a clock that had stopped there, on the one evening somebody would want to
    see that it had not.
    """
    worked = (Segment(1, at(18), None),)
    cells = strip(ledger(segments=worked), 12, now=at(20, 30))

    assert render(cells) == "-----------X", "the session still shows up to the edge"
    assert Cell.LIVE not in cells


def test_a_short_session_lights_a_whole_cell() -> None:
    """It shows presence rather than proportion, so nothing vanishes."""
    worked = (Segment(1, at(9, 5), at(9, 10)),)
    assert render(strip(ledger(segments=worked), 12, now=EVENING)) == "--X------|--"


def test_the_target_tick_marks_when_hours_are_met() -> None:
    """It says when you can go home, allowing for the breaks you took."""
    worked = (Segment(1, at(9), at(12)), Segment(2, at(13), at(15)))
    # 09:00 + 7h24 contracted + 1h break = 17:24, inside the 17:00 cell
    cells = strip(ledger(segments=worked), 12, now=EVENING)
    assert cells[10] is Cell.TARGET


def test_the_target_tick_never_paints_over_the_work_it_lands_in() -> None:
    """Still at your desk when the hours are met, which is the ordinary case.

    The tick is a prediction and the session is a record. Overwriting the cell
    would take a real hour of work out of the row to make room for the moment it
    stopped being compulsory, and the strip would read as an hour short.
    """
    worked = (Segment(1, at(9), at(17)),)  # contracted hours met at 16:24
    cells = strip(ledger(segments=worked), 12, now=EVENING)

    assert render(cells) == "--XXXXXXXX--"
    assert Cell.TARGET not in cells


def test_no_target_tick_before_arriving() -> None:
    """It has no answer to when you can leave before you have arrived."""
    assert Cell.TARGET not in strip(ledger(), 12, now=EVENING)


def test_no_target_tick_on_a_day_that_expects_nothing() -> None:
    """It draws no go-home tick on a day with no hours to meet."""
    worked = (Segment(1, at(9), at(11)),)
    nothing_expected = ledger(segments=worked, expected=timedelta())
    assert Cell.TARGET not in strip(nothing_expected, 12, now=EVENING)


def test_no_target_tick_when_the_hours_run_past_the_window() -> None:
    """A late start puts going-home off the end of the strip, so it is not drawn.

    Clocking in at four leaves contracted hours ending at 23:24, and the window
    the user chose to draw stops at seven. The tick is dropped rather than
    pinned to the last cell, which would read as "you can leave at 19:00" — the
    one answer that is both plausible and wrong.
    """
    late = (Segment(1, at(16), at(18)),)
    assert Cell.TARGET not in strip(ledger(segments=late), 12, now=EVENING)


def test_a_window_can_be_parsed_and_measured() -> None:
    """It reads the configured day window."""
    window = Window.parse("08:00", "18:30")
    assert window.minutes == 630
