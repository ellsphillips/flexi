"""The two mornings a year when a British clock is not monotonic.

Europe/London changes at 01:00/02:00 local, so an ordinary working day never
crosses a transition -- 09:00 to 17:00 on the October Sunday really is eight
hours. What crosses is a night shift, and far more commonly a session somebody
left open overnight. Inside that window the old wall-time arithmetic credited an
hour never worked in March, lost one in October, and on the morning the clocks
went back it ran a live session backwards and then deleted it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from flexi import wallclock
from flexi.models.database.db import ClockEvent
from flexi.models.database.moment import moment_of
from flexi.services.clock import ClockService

pytestmark = pytest.mark.usefixtures("in_london")

FALLBACK = "2026-10-25"  # 02:00 BST -> 01:00 GMT
SPRING = "2026-03-29"  # 01:00 GMT -> 02:00 BST


def _at(iso: str) -> datetime:
    """A UTC instant, so the test says which of the two 01:30s it means."""
    return datetime.fromisoformat(iso).replace(tzinfo=UTC)


def _worked(session: Session, opened: datetime, closed: datetime) -> timedelta:
    """Real elapsed time between the two punches, as the database holds them."""
    service = ClockService(session)
    assert service.clock_in(now=opened).success
    assert service.clock_out(now=closed).success
    punches = sorted(
        moment_of(event)
        for event in session.query(ClockEvent).order_by(ClockEvent.id).all()
    )
    return punches[-1] - punches[0]


def test_a_normal_day_on_the_fallback_sunday_is_eight_hours(session: Session) -> None:
    """The premise test. The transition is at 02:00, so a working day misses it."""
    assert _worked(
        session, _at(f"{FALLBACK}T09:00"), _at(f"{FALLBACK}T17:00")
    ) == timedelta(hours=8)


def test_a_night_shift_across_the_fallback_is_nine_hours(session: Session) -> None:
    """22:00 BST to 06:00 GMT. Wall arithmetic says eight; it was nine."""
    assert _worked(
        session, _at("2026-10-24T21:00"), _at(f"{FALLBACK}T06:00")
    ) == timedelta(hours=9)


def test_a_night_shift_across_the_spring_forward_is_seven_hours(
    session: Session,
) -> None:
    """22:00 GMT to 06:00 BST. Wall arithmetic says eight; it was seven."""
    assert _worked(
        session, _at("2026-03-28T22:00"), _at(f"{SPRING}T05:00")
    ) == timedelta(hours=7)


def test_the_two_hour_span_on_the_spring_sunday(session: Session) -> None:
    """00:30 GMT to 03:30 BST. The audit-facing direction: it used to credit three."""
    assert _worked(
        session, _at(f"{SPRING}T00:30"), _at(f"{SPRING}T02:30")
    ) == timedelta(hours=2)


def test_the_three_and_a_half_hour_span_on_the_fallback_sunday(
    session: Session,
) -> None:
    """00:30 BST to 03:00 GMT."""
    assert _worked(
        session, _at("2026-10-24T23:30"), _at(f"{FALLBACK}T03:00")
    ) == timedelta(hours=3, minutes=30)


def test_a_session_in_the_hour_that_happens_twice_is_not_discarded(
    session: Session,
) -> None:
    """In at 01:30 BST, out forty real minutes later, when the wall reads 01:10.

    The wall span is minus twenty minutes, which tripped the finger-slip guard
    and voided the row with a message blaming the user. There is no unvoid path.
    """
    service = ClockService(session)
    service.clock_in(now=_at(f"{FALLBACK}T00:30"))
    result = service.clock_out(now=_at(f"{FALLBACK}T01:10"))

    assert result.success
    assert result.message == "Clocked out"
    assert result.session is not None
    assert result.session.voided is False


def test_the_two_readings_of_half_past_one_are_stored_apart(session: Session) -> None:
    """The structural claim. Nothing downstream can recover this if it is lost."""
    service = ClockService(session)
    service.clock_in(now=_at(f"{FALLBACK}T00:30"))  # 01:30 BST
    service.clock_out(now=_at(f"{FALLBACK}T01:30"))  # 01:30 GMT

    stored = session.query(ClockEvent).order_by(ClockEvent.id).all()
    assert stored[0].timestamp.hour == 1
    assert stored[1].timestamp.hour == 1
    assert stored[0].utc_offset_minutes == 60
    assert stored[1].utc_offset_minutes == 0


@pytest.mark.parametrize(
    ("elapsed", "expected"),
    [(0, 0), (15, 15), (29, 29), (30, 30), (45, 45), (60, 60), (90, 90)],
)
def test_an_open_session_ticks_forward_through_the_fallback_hour(
    session: Session, elapsed: int, expected: int
) -> None:
    """It used to read 0:00 for a full real hour, then jump."""
    from flexi.domain.balance import worked_from
    from flexi.services.ledger import _segment

    service = ClockService(session)
    service.clock_in(now=_at(f"{FALLBACK}T00:30"))
    open_session = service.get_open_session()
    assert open_session is not None

    now = _at(f"{FALLBACK}T00:30") + timedelta(minutes=elapsed)
    worked = worked_from((_segment(open_session),), wallclock.local(now))
    assert worked == timedelta(minutes=expected)


def test_a_backwards_session_is_refused_rather_than_voided(session: Session) -> None:
    """A negative span is a fault in the data, not a slip of the finger."""
    service = ClockService(session)
    service.clock_in(now=_at(f"{FALLBACK}T10:00"))
    result = service.clock_out(now=_at(f"{FALLBACK}T09:00"))

    assert not result.success
    assert "earlier than the clock-in" in result.message
    assert result.session is not None
    assert result.session.voided is False
