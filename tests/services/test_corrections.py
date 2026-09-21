"""Recording work that was not clocked at the time.

A correction counts for everything a punched session counts for, stays
distinguishable from one, and cannot be used to claim the same hour twice.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta

import pytest
import time_machine
from sqlalchemy.orm import Session

from flexi.constants import AbsenceType, EventSource, Portion
from flexi.domain.format import short_date
from flexi.domain.punch import Cell, Window, strip
from flexi.models.database.db import ClockEvent
from flexi.services.clock import (
    CORRECTION_BACKWARDS,
    CORRECTION_BOOKED,
    CORRECTION_EMPTY,
    CORRECTION_FUTURE,
    ClockService,
)
from flexi.services.registry import Services, build_services
from tests.services.conftest import Configured

SUNDAY = date(2026, 6, 7)
MONDAY = date(2026, 6, 8)
TUESDAY = date(2026, 6, 9)
TODAY = date(2026, 6, 11)
NOW = datetime.combine(TODAY, time(10, 0), tzinfo=UTC)


@pytest.fixture(autouse=True)
def _on_the_day() -> Iterator[None]:
    """Hold the clock at TODAY, which every test here already passes as `now`.

    Every `correct` call names its own day, but the tests that also punch call
    `clock_in()` bare. `tests/services/conftest.py` seeds a bank holiday on 31
    August and `clock_in` refuses one, so an unpinned clock makes those tests
    depend on the date the suite runs on.
    """
    with time_machine.travel(NOW, tick=False):
        yield


@pytest.fixture
def services(configure: Configured) -> Services:
    return configure(leave_year_start="01-01", entitlement=(2026, 25.0))


@pytest.fixture
def clock(services: Services) -> ClockService:
    return services.clock


# What it records ------------------------------------------------------------


def test_corrections_count_as_work(clock: ClockService, session: Session) -> None:
    """It is the same hours; only the way they were captured differs."""
    result = clock.correct(MONDAY, time(9, 0), time(12, 30), now=TODAY)

    assert result.success is True
    assert "3:30" in result.message
    day = build_services(session).ledger.day(MONDAY)
    assert day.worked == timedelta(hours=3, minutes=30)


def test_corrections_are_marked_as_amended(
    clock: ClockService, session: Session
) -> None:
    """Both of its events say so, which is what the strip and the review read."""
    clock.correct(MONDAY, time(9, 0), time(12, 30), now=TODAY)

    sources = {row.source for row in session.query(ClockEvent)}
    assert sources == {EventSource.AMENDED}
    [segment] = build_services(session).ledger.day(MONDAY).segments
    assert segment.amended is True


def test_two_corrections_can_share_a_day(clock: ClockService) -> None:
    """The index that admits one open session says nothing about closed ones."""
    assert clock.correct(MONDAY, time(9, 0), time(12, 30), now=TODAY).success
    assert clock.correct(MONDAY, time(13, 30), time(17, 0), now=TODAY).success

    assert len(clock.segments_on(MONDAY)) == 2


# What it refuses ------------------------------------------------------------


@pytest.mark.parametrize(
    ("opened", "closed", "refusal"),
    [
        (time(17, 0), time(9, 0), CORRECTION_BACKWARDS),
        (time(9, 0), time(9, 0), CORRECTION_EMPTY),
    ],
)
def test_inverted_or_empty_windows_are_refused(
    clock: ClockService, opened: time, closed: time, refusal: str
) -> None:
    """Neither is a typo worth guessing at: one is inverted, one is nothing."""
    result = clock.correct(MONDAY, opened, closed, now=TODAY)

    assert result.success is False
    assert result.message == refusal
    assert clock.segments_on(MONDAY) == []


def test_future_day_cannot_be_corrected(clock: ClockService) -> None:
    """Work recorded forward is not a correction, it is a plan."""
    result = clock.correct(
        TODAY + timedelta(days=1), time(9, 0), time(17, 0), now=TODAY
    )

    assert result.success is False
    assert result.message == CORRECTION_FUTURE


def test_today_can_still_be_corrected(clock: ClockService) -> None:
    """The commonest correction of all is the morning you forgot, this morning."""
    assert clock.correct(TODAY, time(9, 0), time(10, 0), now=TODAY).success is True


@pytest.mark.parametrize(
    ("opened", "closed"),
    [
        (time(12, 0), time(13, 0)),  # starts inside
        (time(8, 0), time(10, 0)),  # ends inside
        (time(8, 0), time(18, 0)),  # swallows it
        (time(10, 0), time(11, 0)),  # inside it
    ],
)
def test_overlapping_corrections_are_refused(
    clock: ClockService, opened: time, closed: time
) -> None:
    """Two stretches sharing an hour is a day that counts it twice."""
    assert clock.correct(MONDAY, time(9, 0), time(12, 30), now=TODAY).success

    result = clock.correct(MONDAY, opened, closed, now=TODAY)

    assert result.success is False
    assert "overlaps" in result.message
    assert len(clock.segments_on(MONDAY)) == 1


def test_corrections_may_touch_end_to_end(clock: ClockService) -> None:
    """Ending at one and starting at one is a break of nothing, not an overlap."""
    assert clock.correct(MONDAY, time(9, 0), time(13, 0), now=TODAY).success

    assert clock.correct(MONDAY, time(13, 0), time(17, 0), now=TODAY).success


def test_past_day_correction_leaves_an_open_session(
    clock: ClockService,
) -> None:
    """They share a table and one partial index, so the open session is untouched."""
    assert clock.clock_in().success
    assert clock.correct(MONDAY, time(9, 0), time(17, 0), now=TODAY).success
    assert clock.is_clocked_in() is True


def test_correction_cannot_claim_a_running_sessions_hours(
    clock: ClockService, session: Session
) -> None:
    """An open session has no end, and that is not the same as no duration.

    A guard reading `first.end or first.start` makes a running session a
    zero-length instant, and the hours inside it are then counted once for the
    punch and once for the correction.
    """
    assert clock.clock_in(now=NOW).success

    result = clock.correct(TODAY, time(10, 0), time(12, 0), now=TODAY)

    assert result.success is False
    assert "overlaps" in result.message
    assert len(clock.segments_on(TODAY)) == 1
    day = build_services(session).ledger.day(
        TODAY, now=datetime.combine(TODAY, time(15, 0), tzinfo=UTC)
    )
    assert day.worked == timedelta(hours=5)


def test_corrections_may_not_run_past_now(
    clock: ClockService, session: Session
) -> None:
    """Hours that have not happened are a plan, and the clock will record them.

    An afternoon meeting typed in at ten and then worked through is the same two
    hours twice: once amended, once punched.
    """
    result = clock.correct(TODAY, time(14, 0), time(16, 0), now=TODAY)

    assert result.success is False
    assert result.message == "A correction cannot run past now"
    assert clock.clock_in(now=datetime.combine(TODAY, time(13, 0), tzinfo=UTC)).success
    assert clock.clock_out(now=datetime.combine(TODAY, time(17, 0), tzinfo=UTC)).success
    assert build_services(session).ledger.day(TODAY).worked == timedelta(hours=4)


def test_night_shift_hours_cannot_be_claimed_twice(
    clock: ClockService,
) -> None:
    """A session belongs to the day it opened, and still spends the next one.

    Ten at night to two in the morning is dated Sunday, so Monday's rows alone
    leave one until two free to be typed in again.
    """
    assert clock.clock_in(now=datetime.combine(SUNDAY, time(22, 0), tzinfo=UTC)).success
    assert clock.clock_out(now=datetime.combine(MONDAY, time(2, 0), tzinfo=UTC)).success

    result = clock.correct(MONDAY, time(1, 0), time(3, 0), now=TODAY)

    assert result.success is False
    assert "overlaps" in result.message
    assert clock.correct(MONDAY, time(2, 0), time(4, 0), now=TODAY).success is True


def test_full_day_off_cannot_also_be_corrected(
    services: Services, clock: ClockService
) -> None:
    """The day is otherwise paid for twice, out of two different balances."""
    assert services.absence.book(MONDAY, AbsenceType.ANNUAL).success

    result = clock.correct(MONDAY, time(9, 0), time(17, 0), now=TODAY)

    assert result.success is False
    assert result.message == CORRECTION_BOOKED.format(day=short_date(MONDAY))
    assert clock.segments_on(MONDAY) == []


def test_booked_morning_leaves_the_afternoon_correctable(
    services: Services, clock: ClockService
) -> None:
    """A booked morning and a worked afternoon is an ordinary day."""
    assert services.absence.book(MONDAY, AbsenceType.ANNUAL, Portion.AM).success

    assert clock.correct(MONDAY, time(13, 0), time(17, 0), now=TODAY).success is True


def test_correction_over_a_booked_morning_is_refused(
    services: Services, clock: ClockService
) -> None:
    """The half is spent out of the allowance; working it again is paid twice.

    The booking side refuses the mirror image through `DayFacts.has_work_in`.
    """
    assert services.absence.book(MONDAY, AbsenceType.ANNUAL, Portion.AM).success

    result = clock.correct(MONDAY, time(9, 0), time(17, 0), now=TODAY)

    assert result.success is False
    booked_off = f"The morning of {short_date(MONDAY)} is already booked off"
    assert result.message == booked_off
    assert clock.segments_on(MONDAY) == []


def test_correction_past_midday_meets_a_booked_afternoon(
    services: Services, clock: ClockService
) -> None:
    """Ending after twelve is what makes a stretch the afternoon's business."""
    assert services.absence.book(MONDAY, AbsenceType.ANNUAL, Portion.PM).success

    result = clock.correct(MONDAY, time(9, 0), time(13, 0), now=TODAY)

    assert result.success is False
    assert "afternoon" in result.message


def test_booked_afternoon_leaves_the_morning_correctable(
    services: Services, clock: ClockService
) -> None:
    """Stopping at twelve is the other half of the same boundary."""
    assert services.absence.book(MONDAY, AbsenceType.ANNUAL, Portion.PM).success

    assert clock.correct(MONDAY, time(9, 0), time(12, 0), now=TODAY).success is True


def test_bank_holiday_can_still_be_corrected(configure: Configured) -> None:
    """A correction is the only way to record work done on a bank holiday.

    `clock_in` refuses one. A correction spends no allowance, so the surplus it
    earns is real.
    """
    services = configure(
        leave_year_start="01-01",
        entitlement=(2026, 25.0),
        holidays=((MONDAY, "A bank holiday"),),
    )

    assert services.clock.correct(MONDAY, time(9, 0), time(17, 0), now=TODAY).success


def test_correction_before_a_running_session_is_allowed(
    clock: ClockService,
) -> None:
    """An open session is worth the rest of its own day, not the whole of it."""
    assert clock.clock_in(now=NOW).success

    assert clock.correct(TODAY, time(7, 0), time(9, 0), now=TODAY).success is True


# How it is drawn ------------------------------------------------------------


def test_corrections_are_drawn_apart_from_punches(
    clock: ClockService, session: Session
) -> None:
    """Same colour, different fill: the hours are the same, the record is not."""
    clock.correct(MONDAY, time(9, 0), time(12, 0), now=TODAY)
    ledger = build_services(session).ledger.day(MONDAY)

    cells = strip(
        ledger, 48, Window(), now=datetime.combine(MONDAY, time(23, 0), tzinfo=UTC)
    )

    assert Cell.AMENDED in cells
    assert Cell.ON not in cells, "a correction is never drawn as a punch"


# Reading them back ----------------------------------------------------------


def test_review_lists_only_what_was_corrected(
    clock: ClockService, session: Session
) -> None:
    """The punch runs a whole afternoon, so it is long enough not to be voided.

    A session under the minimum would be rejected for its length, not for being
    unamended.
    """
    clock.correct(MONDAY, time(9, 0), time(12, 0), now=TODAY)
    clock.clock_in(now=datetime.combine(MONDAY, time(13, 0), tzinfo=UTC))
    clock.clock_out(now=datetime.combine(MONDAY, time(17, 0), tzinfo=UTC))

    found = clock.corrections_between(MONDAY, TODAY)

    assert [one.start.date() for one in found] == [MONDAY]
    assert all(one.amended for one in found)


def test_review_is_ordered_and_bounded_by_period(clock: ClockService) -> None:
    """The review answers for the span on screen, earliest first."""
    clock.correct(TUESDAY, time(9, 0), time(10, 0), now=TODAY)
    clock.correct(MONDAY, time(9, 0), time(10, 0), now=TODAY)

    assert [one.start.date() for one in clock.corrections_between(MONDAY, TUESDAY)] == [
        MONDAY,
        TUESDAY,
    ]
    assert clock.corrections_between(TODAY, TODAY) == []


def test_open_overnight_session_refuses_next_day_correction(
    clock: ClockService, session: Session
) -> None:
    assert clock.clock_in(now=datetime(2026, 6, 7, 22, tzinfo=UTC)).success

    result = clock.correct(MONDAY, time(1), time(2))

    assert not result.success
    assert "overlaps" in result.message
    assert clock.is_clocked_in()
    assert session.query(ClockEvent).count() == 1
    assert clock.clock_out(now=datetime(2026, 6, 8, 3, tzinfo=UTC)).success
    assert build_services(session).ledger.day(SUNDAY).worked == timedelta(hours=5)


def test_correction_can_end_at_an_open_overnight_sessions_start(
    clock: ClockService,
) -> None:
    assert clock.clock_in(now=datetime(2026, 6, 7, 22, tzinfo=UTC)).success
    assert clock.correct(SUNDAY, time(21), time(22)).success
    assert clock.is_clocked_in()
    assert clock.clock_out(now=datetime(2026, 6, 8, 3, tzinfo=UTC)).success
    assert len(clock.segments_on(SUNDAY)) == 2


@pytest.mark.usefixtures("in_london")
def test_open_session_correction_guard_distinguishes_repeated_dst_hour(
    clock: ClockService,
) -> None:
    day = date(2026, 10, 25)
    with time_machine.travel(datetime(2026, 10, 25, 3, tzinfo=UTC), tick=False):
        # The running session starts during the second 01:00 hour (GMT).
        assert clock.clock_in(now=datetime(2026, 10, 25, 1, 30, tzinfo=UTC)).success
        # The first 01:45 (BST) precedes that start despite its wall reading.
        assert clock.correct(day, time(1, 0), time(1, 45)).success
        result = clock.correct(day, time(1, 35, fold=1), time(1, 45, fold=1))
        assert not result.success
        assert "overlaps" in result.message
        assert clock.is_clocked_in()
