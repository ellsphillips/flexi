from datetime import date, datetime, time, timedelta

import pytest

from flexi import wallclock
from flexi.constants import AbsenceType, DayKind, Portion
from flexi.domain.balance import (
    BalanceSummary,
    accumulate,
    expected_for,
    toil_taken_for,
    worked_from,
)
from flexi.domain.format import delta as fmt_delta
from flexi.domain.ledger import AbsenceSlice, DayLedger, Segment

CONTRACTED = timedelta(hours=7, minutes=24)
DAY = date(2026, 6, 11)


def at(hour: int, minute: int = 0) -> datetime:
    """A local reading, carrying its offset. The domain refuses naive moments."""
    return wallclock.local(datetime.combine(DAY, time(hour, minute)))


def slice_(kind: AbsenceType, portion: Portion = Portion.FULL) -> AbsenceSlice:
    return AbsenceSlice(1, kind, portion)


# Expected -------------------------------------------------------------------


def test_untracked_day_expects_nothing() -> None:
    """A leave year that opened before Flexi was installed is not a deficit."""
    assert (
        expected_for(
            CONTRACTED, is_tracked=False, is_working_day=True, is_holiday=False
        )
        == timedelta()
    )


def test_ordinary_working_day_expects_the_contract() -> None:
    assert (
        expected_for(CONTRACTED, is_tracked=True, is_working_day=True, is_holiday=False)
        == CONTRACTED
    )


@pytest.mark.parametrize(
    ("working", "holiday"),
    [(False, False), (True, True)],
)
def test_non_working_day_expects_nothing(working: bool, holiday: bool) -> None:
    """A weekend and a bank holiday both ask for nothing."""
    assert (
        expected_for(
            CONTRACTED, is_tracked=True, is_working_day=working, is_holiday=holiday
        )
        == timedelta()
    )


@pytest.mark.parametrize("kind", list(AbsenceType))
def test_full_day_absence_expects_nothing(kind: AbsenceType) -> None:
    got = expected_for(
        CONTRACTED,
        is_tracked=True,
        is_working_day=True,
        is_holiday=False,
        absences=[slice_(kind)],
    )
    assert got == timedelta()


def test_half_day_expects_half_the_contract() -> None:
    got = expected_for(
        CONTRACTED,
        is_tracked=True,
        is_working_day=True,
        is_holiday=False,
        absences=[slice_(AbsenceType.ANNUAL, Portion.AM)],
    )
    assert got == CONTRACTED / 2


def test_two_half_days_expect_nothing() -> None:
    """A sick morning and an annual afternoon cover the whole day."""
    got = expected_for(
        CONTRACTED,
        is_tracked=True,
        is_working_day=True,
        is_holiday=False,
        absences=[
            slice_(AbsenceType.SICK, Portion.AM),
            slice_(AbsenceType.ANNUAL, Portion.PM),
        ],
    )
    assert got == timedelta()


# Worked ---------------------------------------------------------------------


def test_worked_counts_an_open_session_to_now() -> None:
    segments = [Segment(1, at(9), at(12)), Segment(2, at(13), None)]
    assert worked_from(segments, now=at(14, 30)) == timedelta(hours=4, minutes=30)


def test_naive_now_is_refused() -> None:
    running = [Segment(1, at(9), None)]
    with pytest.raises(ValueError, match="now must be timezone-aware"):
        worked_from(running, now=datetime.combine(DAY, time(14, 30)))


def test_worked_is_zero_without_sessions() -> None:
    assert worked_from([], now=at(17)) == timedelta()


# TOIL -----------------------------------------------------------------------


def test_toil_is_the_only_absence_that_withdraws() -> None:
    assert toil_taken_for(CONTRACTED, [slice_(AbsenceType.FLEXI)]) == CONTRACTED
    assert toil_taken_for(CONTRACTED, [slice_(AbsenceType.ANNUAL)]) == timedelta()


def test_half_toil_day_withdraws_half() -> None:
    got = toil_taken_for(CONTRACTED, [slice_(AbsenceType.FLEXI, Portion.PM)])
    assert got == CONTRACTED / 2


# Accumulation ---------------------------------------------------------------


def day(
    worked: timedelta = timedelta(),
    expected: timedelta = CONTRACTED,
    toil: timedelta = timedelta(),
) -> DayLedger:
    return DayLedger(
        date=DAY,
        kind=DayKind.WORKING,
        is_working_day=True,
        contracted=CONTRACTED,
        worked=worked,
        expected=expected,
        toil_taken=toil,
    )


def test_worked_weekend_is_all_surplus() -> None:
    saturday = day(worked=timedelta(hours=3), expected=timedelta())
    assert saturday.balance_effect == timedelta(hours=3)


def test_hand_worked_fortnight() -> None:
    """A fortnight totalled the way a person would on paper.

    Week one: five ordinary days, one of them 48 minutes long.
    Week two: a TOIL day, a bank holiday, and three days on the nose.
    """
    week_one = [
        day(worked=CONTRACTED),
        day(worked=CONTRACTED + timedelta(minutes=48)),
        day(worked=CONTRACTED),
        day(worked=timedelta(hours=3, minutes=10)),
        day(worked=CONTRACTED),
    ]
    week_two = [
        day(worked=timedelta(), expected=timedelta(), toil=CONTRACTED),
        day(worked=timedelta(), expected=timedelta()),
        day(worked=CONTRACTED),
        day(worked=CONTRACTED),
        day(worked=CONTRACTED),
    ]

    total = accumulate(week_one + week_two)

    #  +48m on Tuesday, −4h14 on Thursday, −7h24 for the TOIL day
    assert (
        total.delta
        == timedelta(minutes=48) - timedelta(hours=4, minutes=14) - CONTRACTED
    )
    assert fmt_delta(total.delta) == "−10:50"
    assert total.is_deficit


def test_summaries_add() -> None:
    """Summaries compose, so a month is the sum of its weeks."""
    one = BalanceSummary(worked=timedelta(hours=8), expected=CONTRACTED)
    two = BalanceSummary(worked=timedelta(hours=7), expected=CONTRACTED)
    assert (one + two).worked == timedelta(hours=15)
    assert (one + two).expected == CONTRACTED * 2


def test_empty_run_is_zero() -> None:
    assert accumulate([]).delta == timedelta()
