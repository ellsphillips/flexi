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
    assert day.breaks == ((at(12), at(13)),)
    assert day.break_total == timedelta(hours=1)


def test_breaks_are_found_whatever_order_the_sessions_arrive_in() -> None:
    """It sorts before pairing, so query order cannot change the answer."""
    day = ledger(segments=(Segment(2, at(13), at(17)), Segment(1, at(9), at(12))))
    assert day.breaks == ((at(12), at(13)),)


def test_two_sessions_that_meet_are_not_a_break() -> None:
    """Clocking out and straight back in is one stretch of work, not a gap.

    It is what somebody does to close a session off at lunchtime and carry on,
    and a zero-length break between them would be drawn on the punch strip and
    added to the go-home time, pushing it later for a lunch nobody took.
    """
    day = ledger(segments=(Segment(1, at(9), at(12)), Segment(2, at(12), at(17))))
    assert day.breaks == ()
    assert day.break_total == timedelta()


def test_a_session_still_running_opens_no_break_behind_it() -> None:
    """A break needs two ends, and an open session has one.

    Sessions are sorted by their start, so a session left open in the morning is
    still the earlier of the pair once a second one is recorded. Reading its
    missing clock-out as the start of a gap is how a `None` reaches the
    subtraction that draws the strip.
    """
    day = ledger(segments=(Segment(1, at(9), None), Segment(2, at(13), at(17))))
    assert day.breaks == ()


def test_a_single_session_has_no_breaks() -> None:
    """It finds no gap where there is only one session."""
    assert ledger(segments=(Segment(1, at(9), at(17)),)).break_total == timedelta()


def test_leave_at_allows_for_breaks() -> None:
    """It answers when you can go home, pushed out by the lunch you took."""
    day = ledger(segments=(Segment(1, at(9), at(12)), Segment(2, at(13), at(17))))
    assert day.leave_at == at(17, 24)


def test_leave_at_is_unknown_before_arriving() -> None:
    """It has no answer before the first clock-in."""
    assert ledger().leave_at is None


def test_first_in_and_last_out() -> None:
    """It bounds the day, counting a running session up to now."""
    day = ledger(segments=(Segment(1, at(9), at(12)), Segment(2, at(13), None)))
    assert day.first_in == at(9)
    assert day.last_out(at(15, 30)) == at(15, 30)
    assert day.is_open


def test_a_day_nobody_clocked_into_has_no_last_out() -> None:
    """``None``, and specifically not ``now``.

    ``last_out`` counts a running session up to the moment it is asked, so the
    obvious wrong answer here is the time the table happened to be drawn — which
    would give every untouched row of a month view a clock-out that crept
    forward on each redraw.
    """
    assert ledger().last_out(at(17)) is None


def test_a_worked_day_summarises_by_how_many_times_you_were_on_the_clock() -> None:
    """A collapsed row still has to distinguish a day that was broken up.

    Eight hours in one stretch and eight hours across three sittings are the
    same total and not the same day, and the count is the only thing in the row
    that says so.
    """
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
    """It separates 'behind on the day' from 'spent from the account'."""
    day = ledger(worked=timedelta(), expected=timedelta(), toil_taken=CONTRACTED)
    assert day.delta == timedelta()
    assert day.balance_effect == -CONTRACTED


def test_a_holiday_summarises_as_its_title() -> None:
    """It names the holiday rather than saying 'holiday'."""
    day = ledger(kind=DayKind.HOLIDAY, holiday_title="Spring bank holiday")
    assert day.is_holiday
    assert day.summary == "Spring bank holiday"


def test_a_booked_day_nobody_worked_summarises_as_what_was_booked() -> None:
    """The row has to say "Annual leave", not the dash an unworked day gets.

    A day off and a working day nobody clocked into are both days with no
    sessions on them, and only the booking tells them apart. Falling through to
    "—" would put a day of leave in the table looking exactly like a day of
    hours quietly missed.
    """
    day = ledger(
        kind=DayKind.ABSENT,
        absences=(AbsenceSlice(1, AbsenceType.ANNUAL, Portion.FULL),),
        expected=timedelta(),
    )
    assert day.summary == "Annual leave"


def test_a_day_split_between_two_reasons_names_both_of_them() -> None:
    """A sick morning after a booked afternoon is two bookings, not one day off.

    Naming only the first would lose the sickness — the half that the person
    did not choose and that a manager may need to see.
    """
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
