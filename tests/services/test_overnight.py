"""Work remains occupied on every date an overnight session reaches."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest
import time_machine
from sqlalchemy.orm import Session

from flexi.constants import AbsenceType, Portion
from flexi.models.database.moment import moment_of
from flexi.services.startup import close_stale_sessions
from tests.services.conftest import Configured

OPENED = datetime(2026, 6, 7, 22, tzinfo=UTC)
TODAY = datetime(2026, 6, 15, 12, tzinfo=UTC)


@pytest.mark.parametrize("nights", [1, 3])
@pytest.mark.parametrize("portion", [Portion.FULL, Portion.AM])
def test_overnight_work_blocks_leave_on_the_date_it_ends(
    configure: Configured, nights: int, portion: Portion
) -> None:
    services = configure()
    closed = (OPENED + timedelta(days=nights)).replace(hour=2)
    assert services.clock.clock_in(now=OPENED).success
    assert services.clock.clock_out(now=closed).success

    result = services.absence.book(closed.date(), AbsenceType.SICK, portion)

    assert not result.success
    assert "recorded work" in result.message
    assert services.absence.book(closed.date(), AbsenceType.SICK, Portion.PM).success


def test_work_ending_at_midnight_leaves_the_following_day_free(
    configure: Configured,
) -> None:
    services = configure()
    closed = (OPENED + timedelta(days=1)).replace(hour=0)
    assert services.clock.clock_in(now=OPENED).success
    assert services.clock.clock_out(now=closed).success

    assert services.absence.book(closed.date(), AbsenceType.SICK).success
    first, following = services.absence.facts_between(OPENED.date(), closed.date())
    assert first.worked
    assert not following.worked


def test_multiple_overnights_cannot_be_claimed_again_as_corrections(
    configure: Configured,
) -> None:
    services = configure()
    closed = (OPENED + timedelta(days=3)).replace(hour=2)
    assert services.clock.clock_in(now=OPENED).success
    assert services.clock.clock_out(now=closed).success

    with time_machine.travel(TODAY, tick=False):
        refused = services.clock.correct(closed.date(), time(1), time(3))
        accepted = services.clock.correct(closed.date(), time(2), time(4))

    assert not refused.success
    assert "overlaps" in refused.message
    assert accepted.success


def test_the_middle_of_a_multiday_session_is_also_occupied(
    configure: Configured,
) -> None:
    services = configure()
    assert services.clock.clock_in(now=OPENED).success
    assert services.clock.clock_out(now=OPENED + timedelta(days=3)).success

    plan = services.absence.plan(date(2026, 6, 8), date(2026, 6, 9), AbsenceType.SICK)

    assert not plan.bookable
    assert len(plan.refused) == 2


@pytest.mark.parametrize("portion", [Portion.FULL, Portion.AM])
def test_clock_out_preserves_work_until_overlapping_leave_is_removed(
    configure: Configured, portion: Portion
) -> None:
    services = configure()
    closed = (OPENED + timedelta(days=1)).replace(hour=2)
    assert services.absence.book(closed.date(), AbsenceType.SICK, portion).success
    opened = services.clock.clock_in(now=OPENED)
    assert opened.success
    assert opened.session is not None

    refused = services.clock.clock_out(now=closed)

    assert not refused.success
    assert "remove that absence" in refused.message
    assert opened.session.clock_out_id is None
    assert services.absence.remove(closed.date(), portion).success
    assert services.clock.clock_out(now=closed).success
    assert opened.session.clock_out_event is not None
    assert moment_of(opened.session.clock_out_event) == closed


def test_clock_out_at_noon_leaves_booked_afternoon_intact(
    configure: Configured,
) -> None:
    services = configure()
    day = date(2026, 6, 8)
    assert services.absence.book(day, AbsenceType.SICK, Portion.PM).success
    assert services.clock.clock_in(now=datetime.combine(day, time(9))).success

    assert services.clock.clock_out(now=datetime.combine(day, time(12))).success
    assert len(services.absence.for_date(day)) == 1


def test_clock_out_after_noon_requires_resolving_booked_afternoon(
    configure: Configured,
) -> None:
    services = configure()
    day = date(2026, 6, 8)
    assert services.absence.book(day, AbsenceType.SICK, Portion.PM).success
    assert services.clock.clock_in(now=datetime.combine(day, time(9))).success

    result = services.clock.clock_out(now=datetime.combine(day, time(13)))

    assert not result.success
    assert "afternoon" in result.message
    assert services.clock.is_clocked_in()


@pytest.mark.parametrize("note", [None, "Morning meeting"])
def test_inferred_clock_out_stops_at_booked_leave_and_explains_it(
    configure: Configured, session: Session, note: str | None
) -> None:
    services = configure()
    day = date(2026, 6, 8)
    assert services.absence.book(day, AbsenceType.SICK, Portion.PM).success
    opened = services.clock.clock_in(now=datetime.combine(day, time(9)))
    assert opened.session is not None
    # Notes can arrive from an earlier correction or imported history.
    opened.session.note = note
    session.commit()

    [closed] = close_stale_sessions(session, time(18), today=day + timedelta(days=1))

    assert closed.clock_out_event is not None
    assert closed.clock_out_event.timestamp.time() == time(12)
    assert closed.note is not None
    assert "Auto-closed at booked afternoon" in closed.note
    if note is not None:
        assert closed.note.startswith(note)
