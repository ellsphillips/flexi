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
    """A local reading, carrying its offset. The domain refuses naive moments.

    This helper used to return a naive datetime, so every test in this file
    exercised a shape `worked_from` documents as forbidden — and the one test
    that counts an open session certified the wall difference coming back,
    which is the opposite of the stated contract.
    """
    return wallclock.local(datetime.combine(DAY, time(hour, minute)))


def slice_(kind: AbsenceType, portion: Portion = Portion.FULL) -> AbsenceSlice:
    return AbsenceSlice(1, kind, portion)


# -- expected --------------------------------------------------------------


def test_a_day_before_flexi_was_tracking_expects_nothing() -> None:
    """A leave year that opened before Flexi was installed is not a deficit.

    Nothing else about the day matters: it is a working day, in the leave year,
    with no absence booked and no holiday on it, and it still asks for nothing,
    because nobody was ever asked to record it.
    """
    assert (
        expected_for(
            CONTRACTED, is_tracked=False, is_working_day=True, is_holiday=False
        )
        == timedelta()
    )


def test_an_ordinary_working_day_expects_the_contract() -> None:
    """It asks for a full day when nothing says otherwise."""
    assert (
        expected_for(CONTRACTED, is_tracked=True, is_working_day=True, is_holiday=False)
        == CONTRACTED
    )


@pytest.mark.parametrize(
    ("working", "holiday"),
    [(False, False), (True, True)],
)
def test_a_non_working_day_expects_nothing(working: bool, holiday: bool) -> None:
    """It asks for nothing on a weekend or a bank holiday."""
    assert (
        expected_for(
            CONTRACTED, is_tracked=True, is_working_day=working, is_holiday=holiday
        )
        == timedelta()
    )


@pytest.mark.parametrize("kind", list(AbsenceType))
def test_a_full_day_absence_of_any_type_expects_nothing(kind: AbsenceType) -> None:
    """It asks for nothing on a booked day, whatever the reason."""
    got = expected_for(
        CONTRACTED,
        is_tracked=True,
        is_working_day=True,
        is_holiday=False,
        absences=[slice_(kind)],
    )
    assert got == timedelta()


def test_a_half_day_expects_half_the_contract() -> None:
    """It halves the ask when half the day is booked."""
    got = expected_for(
        CONTRACTED,
        is_tracked=True,
        is_working_day=True,
        is_holiday=False,
        absences=[slice_(AbsenceType.ANNUAL, Portion.AM)],
    )
    assert got == CONTRACTED / 2


def test_two_half_days_of_different_types_expect_nothing() -> None:
    """It handles a sick morning and an annual afternoon."""
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


# -- worked ----------------------------------------------------------------


def test_worked_counts_an_open_session_up_to_now() -> None:
    """It ticks up while a session is running, which is what makes it live."""
    segments = [Segment(1, at(9), at(12)), Segment(2, at(13), None)]
    assert worked_from(segments, now=at(14, 30)) == timedelta(hours=4, minutes=30)


def test_a_naive_now_is_refused_rather_than_guessed_at() -> None:
    """The refusal the signature is shaped to force, and nothing pinned it."""
    running = [Segment(1, at(9), None)]
    with pytest.raises(TypeError):
        worked_from(running, now=datetime.combine(DAY, time(14, 30)))


def test_worked_is_zero_for_a_day_with_no_sessions() -> None:
    """It totals nothing when nobody clocked in."""
    assert worked_from([], now=at(17)) == timedelta()


# -- TOIL ------------------------------------------------------------------


def test_toil_is_the_only_absence_that_withdraws() -> None:
    """It draws only TOIL from the flexi balance."""
    assert toil_taken_for(CONTRACTED, [slice_(AbsenceType.FLEXI)]) == CONTRACTED
    assert toil_taken_for(CONTRACTED, [slice_(AbsenceType.ANNUAL)]) == timedelta()


def test_a_half_toil_day_withdraws_half() -> None:
    """It withdraws in proportion to the portion booked."""
    got = toil_taken_for(CONTRACTED, [slice_(AbsenceType.FLEXI, Portion.PM)])
    assert got == CONTRACTED / 2


# -- accumulation ----------------------------------------------------------


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


def test_a_worked_weekend_is_all_surplus() -> None:
    """It banks the lot on a day that expected nothing."""
    saturday = day(worked=timedelta(hours=3), expected=timedelta())
    assert saturday.balance_effect == timedelta(hours=3)


def test_a_hand_worked_fortnight() -> None:
    """It totals a fortnight the way a person would on paper.

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
    """It composes, so a month is the sum of its weeks."""
    one = BalanceSummary(worked=timedelta(hours=8), expected=CONTRACTED)
    two = BalanceSummary(worked=timedelta(hours=7), expected=CONTRACTED)
    assert (one + two).worked == timedelta(hours=15)
    assert (one + two).expected == CONTRACTED * 2


def test_an_empty_run_is_zero() -> None:
    """It totals nothing without special-casing."""
    assert accumulate([]).delta == timedelta()
