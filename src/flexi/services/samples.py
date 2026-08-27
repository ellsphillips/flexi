"""A plausible working life, for demos, screenshots and snapshot tests.

`seed_demo` is the whole of what this module offers. The steps under it are its
body broken up to be readable, and they run in an order nothing enforces --
wiping before settings, settings before work, absences before the work that has
to skip them. Publishing them would advertise a sequence a caller could get
wrong, which is the one case where private earns its keep here.

Deterministic by construction -- no ``random``, no clock reads. Every figure is
derived from the day's index, so the seed produces byte-identical output on any
machine on any day, which is what a committed SVG snapshot requires.

Deterministic is not the same as fixed, and this module used to confuse the two.
Everything is derived from the anchor it is handed: the leave year it falls in,
the bank holidays of that year by their own rules, the absences around it.
``flexi --demo`` hands it today and gets a working life ending today; the
screenshots hand it :data:`ANCHOR` and get the same bytes they got last year.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from flexi.constants import DEFAULT_DIVISION, AbsenceType, ClockAction, Portion
from flexi.domain import leaveyear
from flexi.models.database.db import (
    DEFAULT_CONTRACTED_MINUTES,
    DEFAULT_WINDOW_END,
    DEFAULT_WINDOW_START,
    AbsenceDay,
    BankHolidayCache,
    ClockEvent,
    LeaveEntitlement,
    Settings,
    WorkSession,
)
from flexi.models.database.moment import punched
from flexi.services.settings import DEFAULT_ENTITLEMENT_DAYS
from flexi.services.transactions import atomic

__all__ = (
    "ANCHOR",
    "ARRIVALS",
    "AUGUST",
    "EXTRAS",
    "FRIDAY",
    "LEAVE_YEAR",
    "LUNCHES",
    "MAY",
    "NOW",
    "TIMEZONE",
    "add_minutes",
    "holidays_in",
    "nth_monday",
    "seed_demo",
)

FRIDAY = 4
"""The last working weekday, as datetime.weekday() numbers them."""

ANCHOR = date(2026, 6, 11)
"""The Thursday the *screenshots* are drawn as at. Mid-week, mid-month,
mid-leave-year, and fixed because a committed SVG cannot move.

`flexi --demo` passes today instead. Everything below is derived from whatever
anchor it is given, so the two uses do not have to agree on a date -- and until
they stopped agreeing, `--demo` seeded six weeks ending on the 11th of June and
then opened on the real current week, which after that date is empty. The
invitation in the README to look around before committing your own data showed
a blank dashboard and a deficit of a working week."""

TIMEZONE = "UTC"
"""The timezone every demo is drawn in.

Flexi records local wall time, so "local" has to be a fixed thing or the demo
moves with the machine: `time_machine` reads a naive target as UTC, which puts
the frozen clock an hour later on a BST laptop than on a UTC runner. Declared
here because both the snapshot suite and `scripts/shoot.py` have to agree on
it, and for a while only one of them pinned it.
"""

NOW = datetime(2026, 6, 11, 14, 32)
"""Early afternoon, with a session open — the state the dashboard is most often
looked at in, and the one that exercises the live edge of the punch strip."""

LEAVE_YEAR = (4, 6)
"""The 6th of April, as a month and a day: the common one, and the default.

Work is generated from the start of the leave year containing the anchor, not
from a fixed number of weeks back. The balance accumulates from that start, so a
seed covering only the last six weeks would score every earlier working day as a
full day's deficit and open the demo on a balance of minus a hundred hours.
"""

# Arrival and departure minutes past 08:00 / 16:00, cycled by day index. Chosen
# to produce a slightly positive balance with two obvious outliers: a long
# Tuesday and a short Thursday.
ARRIVALS = (42, 55, 38, 61, 47, 52, 44, 58, 40, 49)
LUNCHES = (45, 30, 60, 40, 55, 35, 50, 45, 40, 30)
EXTRAS = (0, 48, 5, -10, 12, 0, 25, -5, 18, 8)

MAY, AUGUST = 5, 8


def nth_monday(year: int, month: int, *, last: bool) -> date:
    """The first or last Monday of a month.

    `Calendar(MONDAY)` explicitly, not the bare `monthcalendar`: that one reads
    a module-global first weekday which `setfirstweekday` mutates, and this
    application lets somebody choose one.
    """
    weeks = calendar.Calendar(calendar.MONDAY).monthdayscalendar(year, month)
    mondays = [week[0] for week in weeks if week[0]]
    return date(year, month, mondays[-1 if last else 0])


def holidays_in(year: int) -> tuple[tuple[date, str], ...]:
    """The three moveable English bank holidays of a leave year, by their rules.

    First and last Monday in May, last Monday in August -- which is what they
    are, rather than three dates typed out for one particular year. A demo
    seeded from a fixed list has bank holidays in the wrong place the moment the
    year moves on, and Flexi refuses to book leave on them, so being wrong about
    one is visible.

    All three fall between April and the following April, so a leave year takes
    them all from its own starting year.
    """
    return (
        (nth_monday(year, MAY, last=False), "Early May bank holiday"),
        (nth_monday(year, MAY, last=True), "Spring bank holiday"),
        (nth_monday(year, AUGUST, last=True), "Summer bank holiday"),
    )


def seed_demo(session: Session, *, anchor: date = ANCHOR) -> None:
    """Replace the database atomically with a life ending on ``anchor``."""
    start = leaveyear.start_of(anchor, *LEAVE_YEAR)
    holidays = {when for when, _ in holidays_in(start.year)}
    with atomic(session):
        _wipe(session)
        _settings(session, anchor)
        _holidays(session, start.year, anchor)
        booked, half_day = _absences(session, anchor, holidays)
        _work(session, start, anchor, booked | holidays, half_day)


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


def _settings(session: Session, anchor: date) -> None:
    month, day = LEAVE_YEAR
    session.add(
        Settings(
            leave_year_start=f"{month:02d}-{day:02d}",
            working_days="0,1,2,3,4",
            bank_holiday_division=DEFAULT_DIVISION.value,
            auto_close_time="18:00",
            contracted_minutes=DEFAULT_CONTRACTED_MINUTES,
            day_window_start=DEFAULT_WINDOW_START,
            day_window_end=DEFAULT_WINDOW_END,
        )
    )
    # The leave year the anchor is in, which is not its calendar year between
    # January and April: an allowance filed under a year that has not started
    # cannot be found by the screen looking for this one's.
    session.add(
        LeaveEntitlement(
            year=leaveyear.active_year(anchor, *LEAVE_YEAR),
            days=DEFAULT_ENTITLEMENT_DAYS,
        )
    )


def _holidays(session: Session, year: int, anchor: date) -> None:
    # Fetched this morning, so the cache reads as fresh whenever the demo is
    # opened. A timestamp from a fixed date would have the command palette's
    # refresh reach for the network on a machine being shown the sample data.
    fetched = datetime.combine(anchor, time(9, 0))
    for when, title in holidays_in(year):
        session.add(
            BankHolidayCache(
                division=DEFAULT_DIVISION,
                date=when,
                title=title,
                fetched_at=fetched,
            )
        )


def _free(when: date, taken: set[date], *, forward: bool = False) -> date:
    """The nearest day nothing else claims, walking away from ``when``.

    A weekend, a bank holiday and a day already booked are all days Flexi
    refuses to put absence on, and a seed that produced a state the application
    would not let you reach is a bad fixture. With a fixed anchor the offsets
    below never landed on one; anchored to today they land on one most weeks.
    """
    step = timedelta(days=1 if forward else -1)
    while when.weekday() > FRIDAY or when in taken:
        when += step
    return when


def _absences(
    session: Session, anchor: date, holidays: set[date]
) -> tuple[set[date], date]:
    """A week off, a sick day, a half day and a TOIL day.

    Returns the whole days that are spoken for, so the work generator skips them
    -- booking absence over recorded work is refused by the service, for the
    same reason -- and the half day, which it draws half of.
    """
    week_off_start = anchor - timedelta(days=anchor.weekday() + 14)
    booked: set[date] = set()

    # Skipping the bank holiday rather than booking over it, which is what
    # `book_range` does and what the README describes: a week off that crosses
    # one takes four days of leave, not five. The fixed anchor's week off began
    # on the Spring bank holiday and the seed booked annual leave on top of it,
    # so the demo showed the one state the application refuses to create.
    for offset in range(5):
        when = week_off_start + timedelta(days=offset)
        if when in holidays:
            continue
        session.add(AbsenceDay(date=when, absence_type=AbsenceType.ANNUAL))
        booked.add(when)

    taken = booked | holidays

    sick = _free(anchor - timedelta(days=6), taken)
    session.add(AbsenceDay(date=sick, absence_type=AbsenceType.SICK))
    booked.add(sick)
    taken.add(sick)

    toil = _free(anchor + timedelta(days=1), taken, forward=True)
    session.add(AbsenceDay(date=toil, absence_type=AbsenceType.FLEXI))
    booked.add(toil)
    taken.add(toil)

    # A half day, so the records table has a PARTIAL row to expand and the punch
    # strip has a day drawn in two colours. Not added to `booked`: half a day off
    # is half a day worked, and the work generator draws the other half.
    half = _free(anchor - timedelta(days=2), taken)
    session.add(
        AbsenceDay(date=half, absence_type=AbsenceType.ANNUAL, portion=Portion.AM)
    )
    return booked, half


def _work(
    session: Session, start: date, anchor: date, booked: set[date], half_day: date
) -> None:
    for index in range((anchor - start).days + 1):
        when = start + timedelta(days=index)
        if when.weekday() > FRIDAY or when in booked:
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
        back = add_minutes(time(12, 30), lunch)
    else:
        back = time(13, 0)

    finish = add_minutes(
        back, DEFAULT_CONTRACTED_MINUTES - (210 if morning else 0) + extra
    )
    _session(session, when, back, finish)


def _open_session(session: Session, when: date, index: int) -> None:
    """Today: arrived, took lunch, and is still on the clock."""
    arrive = time(8, ARRIVALS[index % len(ARRIVALS)] % 60)
    _session(session, when, arrive, time(12, 40))
    _session(session, when, time(13, 20), None)


def _session(session: Session, when: date, start: time, end: time | None) -> None:
    clock_in = punched(ClockAction.IN, datetime.combine(when, start))
    session.add(clock_in)
    session.flush()

    clock_out_id = None
    if end is not None:
        clock_out = punched(ClockAction.OUT, datetime.combine(when, end))
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


def add_minutes(base: time, minutes: int) -> time:
    total = base.hour * 60 + base.minute + minutes
    return time(min(23, total // 60), total % 60)
