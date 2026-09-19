"""A plausible working life, for demos, screenshots and snapshot tests.

`seed_demo` is the module's only mutation and composition boundary. The public
constants and pure date helpers describe its deterministic scenario; the
private steps under it are its body broken up to be readable, and they run in
an order nothing enforces -- wiping before settings, settings before work,
absences before the work that has to skip them. Publishing those stages would
advertise a sequence a caller could get wrong, which is the one case where
private earns its keep here.

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
    BalanceAdjustment,
    BankHolidayAttempt,
    BankHolidayCache,
    BankHolidayRefresh,
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


def _easter(year: int) -> date:
    """Easter Sunday, by the Gregorian computus.

    A rule rather than a table, so Good Friday and Easter Monday sit where they
    belong in whichever year the demo is run in.

    Examples:
        >>> _easter(2026)
        datetime.date(2026, 4, 5)
        >>> _easter(2027)
        datetime.date(2027, 3, 28)
    """
    golden = year % 19
    century, within = divmod(year, 100)
    leaps, century_day = divmod(century, 4)
    correction = (century + 8) // 25
    lunar = (century - correction + 1) // 3
    epact = (19 * golden + century - leaps - lunar + 15) % 30
    quarters, spare = divmod(within, 4)
    weekday = (32 + 2 * century_day + 2 * quarters - epact - spare) % 7
    offset = (golden + 11 * epact + 22 * weekday) // 451
    month, day = divmod(epact + weekday - 7 * offset + 114, 31)
    return date(year, month, day + 1)


def _free(when: date, taken: set[date], *, forward: bool = False) -> date:
    """The nearest day nothing else claims, walking away from ``when``.

    A weekend, a bank holiday and a day already booked are all days Flexi
    refuses to put absence on, and a seed that produced a state the application
    would not let you reach is a bad fixture. With a fixed anchor the offsets
    below never landed on one; anchored to today they land on one most weeks.

    The same walk keeps a fixed bank holiday on the next free weekday, which is
    what a substitute day is.
    """
    step = timedelta(days=1 if forward else -1)
    while when.weekday() > FRIDAY or when in taken:
        when += step
    return when


def _dated_holidays(year: int) -> tuple[tuple[date, str], ...]:
    """The English bank holidays of one calendar year, substitutes resolved.

    A fixed date landing on a weekend is kept on the next free weekday, which is
    why both Christmas Day and Boxing Day move in a year Christmas is a Saturday.
    """
    easter = _easter(year)
    moveable = (
        (easter - timedelta(days=2), "Good Friday"),
        (easter + timedelta(days=1), "Easter Monday"),
        (nth_monday(year, MAY, last=False), "Early May bank holiday"),
        (nth_monday(year, MAY, last=True), "Spring bank holiday"),
        (nth_monday(year, AUGUST, last=True), "Summer bank holiday"),
    )
    taken = {when for when, _ in moveable}
    kept: list[tuple[date, str]] = []
    for when, title in (
        (date(year, 1, 1), "New Year's Day"),
        (date(year, 12, 25), "Christmas Day"),
        (date(year, 12, 26), "Boxing Day"),
    ):
        substitute = _free(when, taken, forward=True)
        taken.add(substitute)
        kept.append((substitute, title))
    return tuple(sorted(moveable + tuple(kept)))


def holidays_in(year: int) -> tuple[tuple[date, str], ...]:
    """The English bank holidays of the leave year beginning in ``year``.

    By their rules -- computus for Easter, the nth Monday of a month for the
    spring and summer holidays, the next free weekday for a fixed date on a
    weekend -- rather than a list typed out for one particular year. A demo
    seeded from a fixed list has bank holidays in the wrong place the moment the
    year moves on, and Flexi refuses to book leave on them and draws them
    differently, so being wrong about one is visible.

    Read over the leave year's own span rather than its starting calendar year.
    New Year's Day belongs to the following one, and Easter falls either side of
    the 6th of April depending on the year: the leave year opening in April 2027
    has no Easter in it at all, and the one opening in April 2028 has two.
    """
    month, day = LEAVE_YEAR
    start, end = date(year, month, day), date(year + 1, month, day)
    return tuple(
        (when, title)
        for when, title in _dated_holidays(year) + _dated_holidays(year + 1)
        if start <= when < end
    )


def seed_demo(
    session: Session, *, anchor: date = ANCHOR, now: time | None = None
) -> None:
    """Replace the database atomically with a life ending on ``anchor``.

    ``now`` is the wall time the anchor day has reached: it decides how much of
    that day is already recorded, and nothing is written past it. A caller
    seeding today has to pass the real one, or the demo opens with a clock-in
    that has not happened and a clock-out key that refuses. The default is
    :data:`NOW`, which is what the screenshots are drawn at.
    """
    moment = NOW.time() if now is None else now
    start = leaveyear.start_of(anchor, *LEAVE_YEAR)
    # Three leave years, because the absences reach a fortnight back and a week
    # forward from the anchor and an anchor near either edge crosses out of its
    # own. Only the anchor's year is cached: those are the dates the demo draws.
    holidays = {
        when
        for year in (start.year - 1, start.year, start.year + 1)
        for when, _ in holidays_in(year)
    }
    with atomic(session):
        _wipe(session)
        _settings(session, anchor, start)
        _holidays(session, start.year, anchor, moment)
        booked, half_day = _absences(session, anchor, holidays)
        _work(session, start, anchor, booked | holidays, half_day, moment)


def _wipe(session: Session) -> None:
    for model in (
        WorkSession,
        ClockEvent,
        AbsenceDay,
        BalanceAdjustment,
        BankHolidayAttempt,
        BankHolidayCache,
        BankHolidayRefresh,
        LeaveEntitlement,
        Settings,
    ):
        session.execute(delete(model))


def _settings(session: Session, anchor: date, start: date) -> None:
    month, day = LEAVE_YEAR
    session.add(
        Settings(
            # The demo has been keeping records since the leave year opened, so
            # that is when it started tracking. Left unset, every seeded day
            # would draw as one Flexi was not there for.
            tracking_since=start,
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


def _holidays(session: Session, year: int, anchor: date, now: time) -> None:
    # Fetched this morning, so the cache reads as fresh whenever the demo is
    # opened. A timestamp from a fixed date would have the command palette's
    # refresh reach for the network on a machine being shown the sample data,
    # and one later than `now` would have it fetched in the future.
    fetched = datetime.combine(anchor, min(time(9, 0), now))
    session.add(
        BankHolidayRefresh(
            division=DEFAULT_DIVISION,
            fetched_at=fetched,
        )
    )
    for when, title in holidays_in(year):
        session.add(
            BankHolidayCache(
                division=DEFAULT_DIVISION,
                date=when,
                title=title,
            )
        )


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
    session: Session,
    start: date,
    anchor: date,
    booked: set[date],
    half_day: date,
    now: time,
) -> None:
    for index in range((anchor - start).days + 1):
        when = start + timedelta(days=index)
        if when.weekday() > FRIDAY or when in booked:
            continue
        if when == anchor:
            _open_session(session, when, index, now)
            continue
        _closed_day(session, when, index, morning=when != half_day)


def _closed_day(session: Session, when: date, index: int, *, morning: bool) -> None:
    """A normal day: in, lunch, out. A half day skips the morning.

    The half day is half a day. Its morning is booked as annual leave, so the
    afternoon owes half a contract; a whole one there draws a day off that also
    earns nearly four hours of flexi and runs past the punch window.
    """
    arrive = time(8, ARRIVALS[index % len(ARRIVALS)] % 60)
    lunch = LUNCHES[index % len(LUNCHES)]
    extra = EXTRAS[index % len(EXTRAS)]

    if morning:
        _session(session, when, arrive, time(12, 30))
        back = add_minutes(time(12, 30), lunch)
        owed = DEFAULT_CONTRACTED_MINUTES - 210
    else:
        back = time(13, 0)
        owed = DEFAULT_CONTRACTED_MINUTES // 2

    _session(session, when, back, add_minutes(back, owed + extra))


def _open_session(session: Session, when: date, index: int, now: time) -> None:
    """The anchor day, as far as ``now`` has taken it.

    Four shapes, and which one a demo opens on depends on the hour it is run at:
    not in yet, on the clock all morning, off the clock at lunch, or back and
    still on the clock. A fixed 13:20 clock-in shown to somebody running the
    demo at nine is a session that has not started, and the clock-out key
    refuses it.
    """
    arrive = time(8, ARRIVALS[index % len(ARRIVALS)] % 60)
    lunch, back = time(12, 40), time(13, 20)
    if now <= arrive:
        return
    if now <= lunch:
        _session(session, when, arrive, None)
        return
    _session(session, when, arrive, lunch)
    if now > back:
        _session(session, when, back, None)


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
