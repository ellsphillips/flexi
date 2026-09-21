"""The two mornings a year when a British clock is not monotonic.

Europe/London changes at 01:00/02:00 local, so an ordinary working day never
crosses a transition. What crosses is a night shift, or a session left open
overnight.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest
import time_machine
from sqlalchemy.orm import Session

from flexi import wallclock
from flexi.constants import ClockAction
from flexi.models.database.db import ClockEvent
from flexi.models.database.moment import moment_of
from flexi.services.clock import CORRECTION_BACKWARDS, CORRECTION_EMPTY
from flexi.services.registry import build_services
from flexi.services.startup import close_stale_sessions

pytestmark = pytest.mark.usefixtures("in_london")

FALLBACK = "2026-10-25"  # 02:00 BST -> 01:00 GMT
SPRING = "2026-03-29"  # 01:00 GMT -> 02:00 BST


def _at(iso: str) -> datetime:
    """A UTC instant, so the test says which of the two 01:30s it means."""
    return datetime.fromisoformat(iso).replace(tzinfo=UTC)


def _worked(session: Session, opened: datetime, closed: datetime) -> timedelta:
    """Real elapsed time between the two punches, as the database holds them."""
    service = build_services(session).clock
    assert service.clock_in(now=opened).success
    assert service.clock_out(now=closed).success
    punches = sorted(
        moment_of(event)
        for event in session.query(ClockEvent).order_by(ClockEvent.id).all()
    )
    return punches[-1] - punches[0]


def test_normal_fallback_sunday_is_eight_hours(session: Session) -> None:
    """The transition is at 02:00, so a working day misses it."""
    assert _worked(
        session, _at(f"{FALLBACK}T09:00"), _at(f"{FALLBACK}T17:00")
    ) == timedelta(hours=8)


def test_night_shift_across_the_fallback_is_nine_hours(session: Session) -> None:
    """22:00 BST to 06:00 GMT: wall arithmetic says eight, the clock says nine."""
    assert _worked(
        session, _at("2026-10-24T21:00"), _at(f"{FALLBACK}T06:00")
    ) == timedelta(hours=9)


def test_night_shift_across_the_spring_is_seven_hours(
    session: Session,
) -> None:
    """22:00 GMT to 06:00 BST: wall arithmetic says eight, the clock says seven."""
    assert _worked(
        session, _at("2026-03-28T22:00"), _at(f"{SPRING}T05:00")
    ) == timedelta(hours=7)


def test_two_hour_span_on_the_spring_sunday(session: Session) -> None:
    """00:30 GMT to 03:30 BST: the skipped hour is not credited."""
    assert _worked(
        session, _at(f"{SPRING}T00:30"), _at(f"{SPRING}T02:30")
    ) == timedelta(hours=2)


def test_three_and_a_half_hours_on_the_fallback_sunday(
    session: Session,
) -> None:
    """00:30 BST to 03:00 GMT."""
    assert _worked(
        session, _at("2026-10-24T23:30"), _at(f"{FALLBACK}T03:00")
    ) == timedelta(hours=3, minutes=30)


def test_session_in_the_repeated_hour_is_kept(session: Session) -> None:
    """In at 01:30 BST, out forty real minutes later, when the wall reads 01:10.

    The wall span is minus twenty minutes, which is what the finger-slip guard
    voids a session for, and there is no unvoid path.
    """
    service = build_services(session).clock
    service.clock_in(now=_at(f"{FALLBACK}T00:30"))
    result = service.clock_out(now=_at(f"{FALLBACK}T01:10"))

    assert result.success
    assert result.message == "Clocked out"
    assert result.session is not None
    assert result.session.voided is False


def test_both_readings_of_half_past_one_are_stored(session: Session) -> None:
    """The offset is stored beside the reading; nothing downstream can recover it."""
    service = build_services(session).clock
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
def test_open_session_ticks_through_the_fallback_hour(
    session: Session, elapsed: int, expected: int
) -> None:
    from flexi.domain.balance import worked_from
    from flexi.services.ledger import segment_of

    service = build_services(session).clock
    service.clock_in(now=_at(f"{FALLBACK}T00:30"))
    open_session = service.get_open_session()
    assert open_session is not None

    now = _at(f"{FALLBACK}T00:30") + timedelta(minutes=elapsed)
    worked = worked_from((segment_of(open_session),), wallclock.local(now))
    assert worked == timedelta(minutes=expected)


def test_backwards_session_is_refused_not_voided(session: Session) -> None:
    """A negative span is rejected without closing or adding to the audit trail."""
    service = build_services(session).clock
    service.clock_in(now=_at(f"{FALLBACK}T10:00"))
    result = service.clock_out(now=_at(f"{FALLBACK}T09:00"))

    assert not result.success
    assert "earlier than the clock-in" in result.message
    assert result.session is not None
    assert result.session.voided is False
    assert result.session.clock_out_id is None
    assert service.get_open_session() is result.session
    assert [event.action for event in session.query(ClockEvent).all()] == [
        ClockAction.IN
    ]


def test_correction_inside_the_lost_hour_records_nothing(session: Session) -> None:
    """The hour the clocks skip has no instants in it.

    01:00 and 02:00 on the spring Sunday name the same moment. As wall readings
    they look an hour apart, and a session of 0:00 walks past the empty guard.
    """
    service = build_services(session).clock
    spring = date.fromisoformat(SPRING)

    with time_machine.travel(_at("2026-03-30T09:00"), tick=False):
        empty = service.correct(spring, time(1, 0), time(2, 0))
        backwards = service.correct(spring, time(1, 30), time(2, 15))

    assert (empty.success, empty.message) == (False, CORRECTION_EMPTY)
    assert (backwards.success, backwards.message) == (False, CORRECTION_BACKWARDS)
    assert session.query(ClockEvent).all() == []


def test_stale_session_in_the_second_repeated_hour_closes_forward(
    session: Session,
) -> None:
    services = build_services(session)
    assert services.clock.clock_in(now=_at(f"{FALLBACK}T01:30")).success

    [closed] = close_stale_sessions(session, time(1, 45), today=date(2026, 10, 26))

    assert closed.clock_out_event is not None
    assert moment_of(closed.clock_out_event) - moment_of(
        closed.clock_in_event
    ) == timedelta(minutes=15)
