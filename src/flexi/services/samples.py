"""A plausible six weeks, for demos, screenshots and snapshot tests.

Deterministic by construction -- no ``random``, no clock reads. Every figure is
derived from the day's index, so the seed produces byte-identical output on any
machine on any day, which is what a committed SVG snapshot requires.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from flexi.constants import AbsenceType, ClockAction, Portion
from flexi.models.database.db import (
    AbsenceDay,
    BankHolidayCache,
    ClockEvent,
    LeaveEntitlement,
    Settings,
    WorkSession,
)

FRIDAY = 4
"""The last working weekday, as datetime.weekday() numbers them."""

ANCHOR = date(2026, 6, 11)
"""The Thursday every demo is drawn as at. Mid-week, mid-month, mid-leave-year."""

NOW = datetime(2026, 6, 11, 14, 32)
"""Early afternoon, with a session open — the state the dashboard is most often
looked at in, and the one that exercises the live edge of the punch strip."""

LEAVE_YEAR_START = date(2026, 4, 6)
"""Work is generated from here, not from a fixed number of weeks back.

The balance accumulates from the start of the leave year, so a seed that only
covered the last six weeks would score every earlier working day as a full day's
deficit and open the demo on a balance of minus a hundred hours.
"""

# Arrival and departure minutes past 08:00 / 16:00, cycled by day index. Chosen
# to produce a slightly positive balance with two obvious outliers: a long
# Tuesday and a short Thursday.
ARRIVALS = (42, 55, 38, 61, 47, 52, 44, 58, 40, 49)
LUNCHES = (45, 30, 60, 40, 55, 35, 50, 45, 40, 30)
EXTRAS = (0, 48, 5, -10, 12, 0, 25, -5, 18, 8)

HOLIDAYS = (
    (date(2026, 5, 4), "Early May bank holiday"),
    (date(2026, 5, 25), "Spring bank holiday"),
    (date(2026, 8, 31), "Summer bank holiday"),
)


def seed_demo(session: Session, *, anchor: date = ANCHOR) -> None:
    """Fill an empty database with six weeks worth of a working life."""
    _wipe(session)
    _settings(session, anchor)
    _holidays(session)
    booked = _absences(session, anchor)
    _work(session, anchor, booked)
    session.commit()


def _wipe(session: Session) -> None:
    for model in (
        WorkSession,
        ClockEvent,
        AbsenceDay,
        BankHolidayCache,
        LeaveEntitlement,
        Settings,
    ):
        session.execute(delete(model))
    session.commit()


def _settings(session: Session, anchor: date) -> None:
    session.add(
        Settings(
            leave_year_start="04-06",
            working_days="0,1,2,3,4",
            bank_holiday_division="england-and-wales",
            auto_close_time="18:00",
            contracted_minutes=444,
            day_window_start="07:00",
            day_window_end="19:00",
        )
    )
    session.add(LeaveEntitlement(year=anchor.year, days=25.0))


def _holidays(session: Session) -> None:
    fetched = datetime(2026, 6, 1, 9, 0)
    for when, title in HOLIDAYS:
        session.add(
            BankHolidayCache(
                division="england-and-wales",
                date=when,
                title=title,
                fetched_at=fetched,
            )
        )


def _absences(session: Session, anchor: date) -> set[date]:
    """A week off, a sick day, a half day and a TOIL day.

    Returns the dates that are spoken for, so the work generator skips them —
    booking absence over recorded work is refused by the service, and a seed that
    produced a state the application would not let you reach is a bad fixture.
    """
    week_off_start = anchor - timedelta(days=anchor.weekday() + 14)
    booked: set[date] = set()

    for offset in range(5):
        when = week_off_start + timedelta(days=offset)
        session.add(AbsenceDay(date=when, absence_type=AbsenceType.ANNUAL))
        booked.add(when)

    sick = anchor - timedelta(days=6)
    session.add(AbsenceDay(date=sick, absence_type=AbsenceType.SICK))
    booked.add(sick)

    toil = anchor + timedelta(days=1)
    session.add(AbsenceDay(date=toil, absence_type=AbsenceType.FLEXI))
    booked.add(toil)

    # A half day, so the records table has a PARTIAL row to expand and the punch
    # strip has a day drawn in two colours.
    half = anchor - timedelta(days=2)
    session.add(
        AbsenceDay(date=half, absence_type=AbsenceType.ANNUAL, portion=Portion.AM)
    )
    return booked


def _work(session: Session, anchor: date, booked: set[date]) -> None:
    holidays = {when for when, _ in HOLIDAYS}
    half_day = anchor - timedelta(days=2)
    start = LEAVE_YEAR_START

    for index in range((anchor - start).days + 1):
        when = start + timedelta(days=index)
        if when.weekday() > FRIDAY or when in booked or when in holidays:
            continue
        if when == anchor:
            _open_session(session, when, index)
            continue
        _closed_day(session, when, index, morning=when != half_day)


def _closed_day(session: Session, when: date, index: int, *, morning: bool) -> None:
    """A normal day: in, lunch, out. A half day skips the morning."""
    arrive = time(8, ARRIVALS[index % len(ARRIVALS)] % 60)
    lunch = LUNCHES[index % len(LUNCHES)]
    extra = EXTRAS[index % len(EXTRAS)]

    if morning:
        _session(session, when, arrive, time(12, 30))
        back = _add_minutes(time(12, 30), lunch)
    else:
        back = time(13, 0)

    finish = _add_minutes(back, 444 - (210 if morning else 0) + extra)
    _session(session, when, back, finish)


def _open_session(session: Session, when: date, index: int) -> None:
    """Today: arrived, took lunch, and is still on the clock."""
    arrive = time(8, ARRIVALS[index % len(ARRIVALS)] % 60)
    _session(session, when, arrive, time(12, 40))
    _session(session, when, time(13, 20), None)


def _session(session: Session, when: date, start: time, end: time | None) -> None:
    clock_in = ClockEvent(
        action=ClockAction.IN,
        timestamp=datetime.combine(when, start, tzinfo=UTC),
        source="user",
    )
    session.add(clock_in)
    session.flush()

    clock_out_id = None
    if end is not None:
        clock_out = ClockEvent(
            action=ClockAction.OUT,
            timestamp=datetime.combine(when, end, tzinfo=UTC),
            source="user",
        )
        session.add(clock_out)
        session.flush()
        clock_out_id = clock_out.id

    session.add(
        WorkSession(
            clock_in_id=clock_in.id,
            clock_out_id=clock_out_id,
            work_date=when,
        )
    )


def _add_minutes(base: time, minutes: int) -> time:
    total = base.hour * 60 + base.minute + minutes
    return time(min(23, total // 60), total % 60)
