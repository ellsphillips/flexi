from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from flexi import wallclock
from flexi.constants import AbsenceType, DayKind, Portion
from flexi.domain.ledger import AbsenceSlice, DayLedger, Segment

DAY = date(2026, 6, 11)
CONTRACTED = timedelta(hours=7, minutes=24)
LONDON = ZoneInfo("Europe/London")


def at(hour: int, minute: int = 0) -> datetime:
    return datetime.combine(DAY, time(hour, minute), tzinfo=UTC)


def ledger(**kwargs: object) -> DayLedger:
    base: dict[str, object] = {
        "date": DAY,
        "kind": DayKind.WORKING,
        "is_working_day": True,
        "contracted": CONTRACTED,
        "worked": timedelta(),
        "expected": CONTRACTED,
    }
    base.update(kwargs)
    return DayLedger(**base)  # type: ignore[arg-type]


def test_an_open_segment_measures_to_now() -> None:
    segment = Segment(1, at(9), None)
    assert segment.is_open
    assert segment.duration(at(11, 30)) == timedelta(hours=2, minutes=30)


def test_a_closed_segment_ignores_now() -> None:
    segment = Segment(1, at(9), at(11))
    assert segment.duration(at(23)) == timedelta(hours=2)


def test_a_segment_requires_timezone_aware_endpoints() -> None:
    naive = datetime.combine(DAY, time(9))
    with pytest.raises(ValueError, match="start must be timezone-aware"):
        Segment(1, naive)
    with pytest.raises(ValueError, match="end must be timezone-aware"):
        Segment(1, at(9), naive)


def test_an_open_segment_requires_an_aware_now() -> None:
    with pytest.raises(ValueError, match="now must be timezone-aware"):
        Segment(1, at(9)).duration(datetime.combine(DAY, time(10)))


def test_endpoints_measure_real_time_across_dst() -> None:
    start = datetime(2026, 10, 24, 22, 0, tzinfo=LONDON)
    end = datetime(2026, 10, 25, 6, 0, tzinfo=LONDON)

    assert Segment(1, start, end).duration(end) == timedelta(hours=9)


def test_breaks_are_only_between_sessions() -> None:
    day = ledger(segments=(Segment(1, at(9), at(12)), Segment(2, at(13), at(17))))
    assert day.breaks == ((at(12), at(13)),)
    assert day.break_total == timedelta(hours=1)


def test_breaks_are_found_in_any_session_order() -> None:
    """Sessions are sorted before pairing, so query order cannot change them."""
    day = ledger(segments=(Segment(2, at(13), at(17)), Segment(1, at(9), at(12))))
    assert day.breaks == ((at(12), at(13)),)


def test_two_sessions_that_meet_are_not_a_break() -> None:
    """A zero-length break is drawn on the strip and pushes the go-home time out."""
    day = ledger(segments=(Segment(1, at(9), at(12)), Segment(2, at(12), at(17))))
    assert day.breaks == ()
    assert day.break_total == timedelta()


def test_an_open_session_opens_no_break() -> None:
    """Sessions sort by start, so an open one can be the earlier of a pair."""
    day = ledger(segments=(Segment(1, at(9), None), Segment(2, at(13), at(17))))
    assert day.breaks == ()


def test_a_single_session_has_no_breaks() -> None:
    assert ledger(segments=(Segment(1, at(9), at(17)),)).break_total == timedelta()


def test_leave_at_allows_for_breaks() -> None:
    day = ledger(segments=(Segment(1, at(9), at(12)), Segment(2, at(13), at(17))))
    assert day.leave_at == at(17, 24)


def test_leave_at_reenters_local_time_after_dst() -> None:
    with wallclock.pinned(LONDON):
        start = wallclock.local(datetime(2026, 3, 29, 0, 30))
        day = ledger(
            date=start.date(),
            segments=(Segment(1, start),),
            expected=CONTRACTED,
        )
        leave_at = day.leave_at

    assert leave_at is not None
    assert (leave_at.hour, leave_at.minute) == (8, 54)
    assert leave_at.utcoffset() == timedelta(hours=1)


def test_leave_at_is_unknown_before_arriving() -> None:
    assert ledger().leave_at is None


def test_leave_at_is_unknown_when_no_work_expected() -> None:
    day = ledger(segments=(Segment(1, at(9)),), expected=timedelta())

    assert day.leave_at is None


def test_first_in_and_last_out() -> None:
    """A running session counts up to the moment it is asked."""
    day = ledger(segments=(Segment(1, at(9), at(12)), Segment(2, at(13), None)))
    assert day.first_in == at(9)
    assert day.last_out(at(15, 30)) == at(15, 30)
    assert day.is_open


def test_an_unworked_day_has_no_last_out() -> None:
    """``now`` would give every untouched row a clock-out that creeps forward."""
    assert ledger().last_out(at(17)) is None


def test_a_worked_day_summarises_its_session_count() -> None:
    """Eight hours in one stretch and across three sittings are not the same day."""
    once = ledger(segments=(Segment(1, at(9), at(17)),))
    thrice = ledger(
        segments=(
            Segment(1, at(9), at(11)),
            Segment(2, at(12), at(14)),
            Segment(3, at(15), at(17)),
        )
    )
    assert once.summary == "1 session"
    assert thrice.summary == "3 sessions"


def test_delta_and_balance_effect_differ_for_toil() -> None:
    """`delta` is behind on the day; `balance_effect` is spent from the account."""
    day = ledger(worked=timedelta(), expected=timedelta(), toil_taken=CONTRACTED)
    assert day.delta == timedelta()
    assert day.balance_effect == -CONTRACTED


def test_a_holiday_summarises_as_its_title() -> None:
    day = ledger(kind=DayKind.HOLIDAY, holiday_title="Spring bank holiday")
    assert day.is_holiday
    assert day.summary == "Spring bank holiday"


def test_a_booked_day_summarises_as_its_booking() -> None:
    """Only the booking tells a day off from an unworked working day."""
    day = ledger(
        kind=DayKind.ABSENT,
        absences=(AbsenceSlice(1, AbsenceType.ANNUAL, Portion.FULL),),
        expected=timedelta(),
    )
    assert day.summary == "Annual leave"


def test_a_split_day_names_both_reasons() -> None:
    """Naming only the first loses the half the person did not choose."""
    day = ledger(
        kind=DayKind.ABSENT,
        absences=(
            AbsenceSlice(1, AbsenceType.SICK, Portion.AM),
            AbsenceSlice(2, AbsenceType.ANNUAL, Portion.PM),
        ),
        expected=timedelta(),
    )
    assert day.summary == "Sickness (morning) · Annual leave (afternoon)"


def test_a_partial_day_summarises_both_halves() -> None:
    day = ledger(
        kind=DayKind.PARTIAL,
        absences=(AbsenceSlice(1, AbsenceType.ANNUAL, Portion.AM),),
        segments=(Segment(1, at(13), at(17)),),
    )
    assert day.summary == "Annual leave (morning) · worked"


def test_an_absence_slice_labels_its_portion() -> None:
    assert AbsenceSlice(1, AbsenceType.SICK, Portion.FULL).label == "Sickness"
    assert AbsenceSlice(1, AbsenceType.SICK, Portion.PM).label == "Sickness (afternoon)"


def test_an_absence_slice_knows_which_half_it_covers() -> None:
    """Portions split the day at midday."""
    morning = AbsenceSlice(1, AbsenceType.SICK, Portion.AM)
    assert morning.covers(at(9))
    assert not morning.covers(at(14))
    assert AbsenceSlice(1, AbsenceType.SICK, Portion.FULL).covers(at(14))


def test_an_empty_working_day_summarises_as_a_dash() -> None:
    """A weekend stays blank."""
    assert ledger().summary == "—"
    assert ledger(is_working_day=False, kind=DayKind.WEEKEND).summary == ""


def test_a_backwards_segment_reports_a_negative_duration() -> None:
    """A ``max(timedelta(), ...)`` clamp would read a disagreeing pair as 0:00."""
    later = datetime(2026, 10, 25, 1, 30, tzinfo=UTC)
    earlier = datetime(2026, 10, 25, 0, 30, tzinfo=UTC)

    backwards = Segment(1, later, earlier)

    assert backwards.duration(later) < timedelta()
    assert backwards.duration(later) == timedelta(hours=-1)
