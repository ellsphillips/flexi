from datetime import UTC, date, datetime, time, timedelta

from flexi.constants import AbsenceType, DayKind, Portion
from flexi.domain.ledger import AbsenceSlice, DayLedger, Segment

DAY = date(2026, 6, 11)
CONTRACTED = timedelta(hours=7, minutes=24)


def at(hour: int, minute: int = 0) -> datetime:
    return datetime.combine(DAY, time(hour, minute))


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
    """It reports the elapsed time at the moment it is asked, not the wall clock."""
    segment = Segment(1, at(9), None)
    assert segment.is_open
    assert segment.duration(at(11, 30)) == timedelta(hours=2, minutes=30)


def test_a_closed_segment_ignores_now() -> None:
    """It reports what it recorded, whenever it is redrawn."""
    segment = Segment(1, at(9), at(11))
    assert segment.duration(at(23)) == timedelta(hours=2)


def test_breaks_are_only_between_sessions() -> None:
    """It does not call the morning before you arrived a break."""
    day = ledger(segments=(Segment(1, at(9), at(12)), Segment(2, at(13), at(17))))
    assert day.breaks() == ((at(12), at(13)),)
    assert day.break_total() == timedelta(hours=1)


def test_breaks_are_found_whatever_order_the_sessions_arrive_in() -> None:
    """It sorts before pairing, so query order cannot change the answer."""
    day = ledger(segments=(Segment(2, at(13), at(17)), Segment(1, at(9), at(12))))
    assert day.breaks() == ((at(12), at(13)),)


def test_a_single_session_has_no_breaks() -> None:
    """It finds no gap where there is only one session."""
    assert ledger(segments=(Segment(1, at(9), at(17)),)).break_total() == timedelta()


def test_leave_at_allows_for_breaks() -> None:
    """It answers when you can go home, pushed out by the lunch you took."""
    day = ledger(segments=(Segment(1, at(9), at(12)), Segment(2, at(13), at(17))))
    assert day.leave_at() == at(17, 24)


def test_leave_at_is_unknown_before_arriving() -> None:
    """It has no answer before the first clock-in."""
    assert ledger().leave_at() is None


def test_first_in_and_last_out() -> None:
    """It bounds the day, counting a running session up to now."""
    day = ledger(segments=(Segment(1, at(9), at(12)), Segment(2, at(13), None)))
    assert day.first_in == at(9)
    assert day.last_out(at(15, 30)) == at(15, 30)
    assert day.is_open


def test_delta_and_balance_effect_differ_for_toil() -> None:
    """It separates 'behind on the day' from 'spent from the account'."""
    day = ledger(worked=timedelta(), expected=timedelta(), toil_taken=CONTRACTED)
    assert day.delta == timedelta()
    assert day.balance_effect == -CONTRACTED


def test_a_holiday_summarises_as_its_title() -> None:
    """It names the holiday rather than saying 'holiday'."""
    day = ledger(kind=DayKind.HOLIDAY, holiday_title="Spring bank holiday")
    assert day.is_holiday
    assert day.summary == "Spring bank holiday"


def test_a_partial_day_summarises_both_halves() -> None:
    """It says a half day was booked and the other half was worked."""
    day = ledger(
        kind=DayKind.PARTIAL,
        absences=(AbsenceSlice(1, AbsenceType.ANNUAL, Portion.AM),),
        segments=(Segment(1, at(13), at(17)),),
    )
    assert day.summary == "Annual leave (morning) · worked"


def test_an_absence_slice_labels_its_portion() -> None:
    """It says which half of the day was booked."""
    assert AbsenceSlice(1, AbsenceType.SICK, Portion.FULL).label == "Sickness"
    assert AbsenceSlice(1, AbsenceType.SICK, Portion.PM).label == "Sickness (afternoon)"


def test_an_absence_slice_knows_which_half_it_covers() -> None:
    """It splits the day at midday."""
    morning = AbsenceSlice(1, AbsenceType.SICK, Portion.AM)
    assert morning.covers(at(9))
    assert not morning.covers(at(14))
    assert AbsenceSlice(1, AbsenceType.SICK, Portion.FULL).covers(at(14))


def test_an_empty_working_day_summarises_as_a_dash() -> None:
    """It marks a working day nobody worked, and stays quiet on a weekend."""
    assert ledger().summary == "—"
    assert ledger(is_working_day=False, kind=DayKind.WEEKEND).summary == ""


def test_a_backwards_segment_reports_a_negative_duration() -> None:
    """The clamp that made every timezone fault silent.

    ``max(timedelta(), ...)`` turned a session whose ends disagreed into a
    clean zero, so an hour of real work read as 0:00 every October and nothing
    ever raised. A negative duration is a fault, and it should look like one.
    """
    later = datetime(2026, 10, 25, 1, 30, tzinfo=UTC)
    earlier = datetime(2026, 10, 25, 0, 30, tzinfo=UTC)

    backwards = Segment(1, later, earlier)

    assert backwards.duration(later) < timedelta()
    assert backwards.duration(later) == timedelta(hours=-1)
