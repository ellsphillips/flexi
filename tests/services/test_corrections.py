"""Recording work nobody clocked at the time.

A morning nobody punched in for is still a morning that was worked, and the
alternative to recording it is a balance that is quietly wrong. What is checked
here is that a correction counts for everything a punched session counts for,
stays distinguishable from one, and cannot be used to claim the same hour twice.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy.orm import Session

from flexi.constants import EventSource
from flexi.domain.punch import Cell, Window, strip
from flexi.models.database.db import ClockEvent
from flexi.services.clock import (
    CORRECTION_BACKWARDS,
    CORRECTION_EMPTY,
    CORRECTION_FUTURE,
    ClockService,
)
from flexi.services.registry import build_services
from tests.services.conftest import Configured

MONDAY = date(2026, 6, 8)
TUESDAY = date(2026, 6, 9)
TODAY = date(2026, 6, 11)


@pytest.fixture
def clock(configure: Configured) -> ClockService:
    return configure(leave_year_start="01-01", entitlement=(2026, 25.0)).clock


# -- what it records ---------------------------------------------------------


def test_a_correction_counts_as_work(clock: ClockService, session: Session) -> None:
    """It is the same hours; only the way they were captured differs."""
    result = clock.correct(MONDAY, time(9, 0), time(12, 30), now=TODAY)

    assert result.success is True
    assert "3:30" in result.message
    day = build_services(session).ledger.day(MONDAY)
    assert day.worked == timedelta(hours=3, minutes=30)


def test_a_correction_is_marked_as_one(clock: ClockService, session: Session) -> None:
    """Both of its events say so, which is what the strip and the review read."""
    clock.correct(MONDAY, time(9, 0), time(12, 30), now=TODAY)

    sources = {row.source for row in session.query(ClockEvent)}
    assert sources == {EventSource.AMENDED}
    [segment] = build_services(session).ledger.day(MONDAY).segments
    assert segment.amended is True


def test_two_corrections_can_share_a_day(clock: ClockService) -> None:
    """A morning and an afternoon are two stretches, not one long one.

    The index that admits a single *open* session says nothing about closed
    ones, which is what lets a day be corrected a piece at a time.
    """
    assert clock.correct(MONDAY, time(9, 0), time(12, 30), now=TODAY).success
    assert clock.correct(MONDAY, time(13, 30), time(17, 0), now=TODAY).success

    assert len(clock.segments_on(MONDAY)) == 2


# -- what it refuses ---------------------------------------------------------


@pytest.mark.parametrize(
    ("opened", "closed", "refusal"),
    [
        (time(17, 0), time(9, 0), CORRECTION_BACKWARDS),
        (time(9, 0), time(9, 0), CORRECTION_EMPTY),
    ],
)
def test_a_window_that_is_not_a_window_is_refused(
    clock: ClockService, opened: time, closed: time, refusal: str
) -> None:
    """Neither is a typo worth guessing at: one is inverted, one is nothing."""
    result = clock.correct(MONDAY, opened, closed, now=TODAY)

    assert result.success is False
    assert result.message == refusal
    assert clock.segments_on(MONDAY) == []


def test_a_day_that_has_not_happened_cannot_be_corrected(clock: ClockService) -> None:
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
def test_a_correction_may_not_claim_an_hour_twice(
    clock: ClockService, opened: time, closed: time
) -> None:
    """Refused rather than merged.

    Two stretches sharing an hour is a day that counts it twice, and no rule for
    reconciling them is better than a person looking at both and saying which is
    right.
    """
    assert clock.correct(MONDAY, time(9, 0), time(12, 30), now=TODAY).success

    result = clock.correct(MONDAY, opened, closed, now=TODAY)

    assert result.success is False
    assert "overlaps" in result.message
    assert len(clock.segments_on(MONDAY)) == 1


def test_a_correction_may_touch_the_end_of_another(clock: ClockService) -> None:
    """Ending at one and starting at one is a break of nothing, not an overlap."""
    assert clock.correct(MONDAY, time(9, 0), time(13, 0), now=TODAY).success

    assert clock.correct(MONDAY, time(13, 0), time(17, 0), now=TODAY).success


def test_a_correction_does_not_collide_with_a_running_session(
    clock: ClockService,
) -> None:
    """Clocking in opens a session; correcting a past day is not that.

    They share a table and one partial index, so a correction written while
    somebody is on the clock has to leave the open session alone.
    """
    assert clock.clock_in().success
    assert clock.correct(MONDAY, time(9, 0), time(17, 0), now=TODAY).success
    assert clock.is_clocked_in() is True


# -- how it is drawn ---------------------------------------------------------


def test_a_corrected_stretch_is_drawn_apart_from_a_punched_one(
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


# -- reading them back -------------------------------------------------------


def test_the_review_lists_only_what_was_corrected(
    clock: ClockService, session: Session
) -> None:
    """A punched session on the same day is not what somebody came to check."""
    clock.correct(MONDAY, time(9, 0), time(12, 0), now=TODAY)
    clock.clock_in()
    clock.clock_out()

    found = clock.corrections_between(MONDAY, TODAY)

    assert [one.start.date() for one in found] == [MONDAY]
    assert all(one.amended for one in found)


def test_the_review_is_ordered_and_bounded_by_the_period(clock: ClockService) -> None:
    """It answers for the span on screen, earliest first."""
    clock.correct(TUESDAY, time(9, 0), time(10, 0), now=TODAY)
    clock.correct(MONDAY, time(9, 0), time(10, 0), now=TODAY)

    assert [one.start.date() for one in clock.corrections_between(MONDAY, TUESDAY)] == [
        MONDAY,
        TUESDAY,
    ]
    assert clock.corrections_between(TODAY, TODAY) == []
