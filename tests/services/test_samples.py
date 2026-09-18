"""The demo seed, against the day it is actually run on.

`flexi --demo` is the README's invitation to look around before committing your
own data, and it seeds a throwaway database and opens the application on today.
The seed was anchored to a fixed Thursday in June 2026, so on any later day it
filled six weeks that had already gone by and opened on an empty current week:
no sessions, no punch strips, and a deficit of a full working week. The first
thing a new person saw was a screen that made the application look broken.

Everything here is checked at a spread of anchors, because the interesting cases
are the ones a fixed date cannot reach -- a Saturday, the day after a bank
holiday, the first week of a leave year, and the last.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from flexi.constants import AbsenceType, Portion
from flexi.models.database.db import (
    DEFAULT_CONTRACTED_MINUTES,
    AbsenceDay,
    BalanceAdjustment,
    BankHolidayCache,
    BankHolidayRefresh,
    ClockEvent,
    LeaveEntitlement,
    Settings,
    WorkSession,
)
from flexi.services import samples
from flexi.services.adjustments import AdjustmentService
from flexi.services.registry import build_services
from flexi.services.samples import ANCHOR, holidays_in, seed_demo

DAY_WINDOW_END = time(19, 0)
"""The right-hand edge of the punch strip. A session running past it is drawn
off the end of the picture."""

ANCHORS = [
    pytest.param(ANCHOR, id="the Thursday the screenshots are taken on"),
    pytest.param(date(2026, 8, 15), id="a Saturday"),
    pytest.param(date(2026, 8, 25), id="the day after a bank holiday"),
    pytest.param(date(2026, 4, 7), id="the second day of a leave year"),
    pytest.param(date(2027, 4, 5), id="the last day of a leave year"),
    pytest.param(date(2028, 2, 29), id="a leap day"),
    pytest.param(date(2027, 1, 14), id="a January anchor, so Christmas is behind it"),
]


def _worked(session: Session) -> set[date]:
    return set(session.execute(select(WorkSession.work_date)).scalars())


def _absent(session: Session) -> set[date]:
    return set(session.execute(select(AbsenceDay.date)).scalars())


def _cached_holidays(session: Session) -> set[date]:
    return set(session.execute(select(BankHolidayCache.date)).scalars())


def _punches(session: Session, when: date) -> list[datetime]:
    """Every clock reading on one date, in order."""
    return sorted(
        moment
        for moment in session.execute(select(ClockEvent.timestamp)).scalars()
        if moment.date() == when
    )


def _holidays_around(when: date) -> set[date]:
    """Every bank holiday the seed could have had to step over.

    Three leave years, because an anchor near either edge of one reaches into
    its neighbour and `holidays_in` answers for a leave year rather than a
    calendar year.
    """
    return {
        day
        for year in (when.year - 1, when.year, when.year + 1)
        for day, _ in holidays_in(year)
    }


def test_reseeding_removes_existing_balance_corrections(session: Session) -> None:
    """Replacing a demo means no state from the previous database survives."""
    AdjustmentService(session).record(ANCHOR, timedelta(hours=2), "old correction")
    assert session.scalars(select(BalanceAdjustment)).all()

    seed_demo(session)

    assert session.scalars(select(BalanceAdjustment)).all() == []


@pytest.mark.parametrize("anchor", ANCHORS)
def test_the_week_the_demo_opens_on_has_work_in_it(
    session: Session, anchor: date
) -> None:
    """The whole point: the screen a person lands on is not empty.

    Asserted over the working days of the anchor's own week, since that is what
    the dashboard shows -- and up to the anchor, because a demo does not record
    days that have not happened.
    """
    seed_demo(session, anchor=anchor)

    monday = anchor - timedelta(days=anchor.weekday())
    worked = _worked(session)
    absent = _absent(session)
    holidays = _holidays_around(monday)

    days = [monday + timedelta(days=n) for n in range(anchor.weekday() + 1)]
    accounted = [
        day
        for day in days
        if day.weekday() <= samples.FRIDAY
        and day not in holidays
        and (day in worked or day in absent)
    ]
    expected = [
        day for day in days if day.weekday() <= samples.FRIDAY and day not in holidays
    ]

    assert accounted == expected


@pytest.mark.parametrize("anchor", ANCHORS)
def test_nothing_is_recorded_after_the_day_it_was_seeded_for(
    session: Session, anchor: date
) -> None:
    """A demo of a working life cannot include work nobody has done yet."""
    seed_demo(session, anchor=anchor)

    latest = max(_worked(session))
    assert latest <= anchor


@pytest.mark.parametrize("anchor", ANCHORS)
def test_no_absence_lands_where_flexi_would_refuse_to_book_one(
    session: Session, anchor: date
) -> None:
    """A fixture the application would not let you build is a bad fixture.

    Weekends and bank holidays are both refused by `book_range`, so a seed that
    put a sick day on a Sunday would be showing a state no user could reach --
    and the offsets that never did with a fixed anchor do most weeks with a
    moving one.
    """
    seed_demo(session, anchor=anchor)

    holidays = _holidays_around(anchor)

    for when in _absent(session):
        assert when.weekday() <= samples.FRIDAY, f"{when:%a %d %b} is a weekend"
        assert when not in holidays, f"{when:%a %d %b} is a bank holiday"


@pytest.mark.parametrize("anchor", ANCHORS)
def test_no_day_is_both_worked_and_taken_off(session: Session, anchor: date) -> None:
    """Except the half day, which is exactly one day and is meant to be both."""
    seed_demo(session, anchor=anchor)

    whole_days = set(
        session.execute(
            select(AbsenceDay.date).where(AbsenceDay.portion == Portion.FULL)
        ).scalars()
    )

    assert whole_days & _worked(session) == set()


@pytest.mark.parametrize("anchor", ANCHORS)
def test_the_sample_has_every_shape_the_screens_are_built_to_draw(
    session: Session, anchor: date
) -> None:
    """A week off, a sick day, a TOIL day and a half day, wherever the anchor is.

    The half day is what gives the records table a row to expand and the punch
    strip a day drawn in two colours, and walking absences off a weekend could
    have quietly dropped one by landing it on a day already taken.
    """
    seed_demo(session, anchor=anchor)

    rows = session.execute(select(AbsenceDay)).scalars().all()
    types = {row.absence_type for row in rows}

    assert {AbsenceType.ANNUAL, AbsenceType.SICK, AbsenceType.FLEXI} <= types
    assert sum(row.portion is not Portion.FULL for row in rows) == 1
    assert len({row.date for row in rows}) == len(rows), "two absences share a date"


@pytest.mark.parametrize("anchor", ANCHORS)
def test_the_leave_year_is_the_one_the_anchor_falls_in(
    session: Session, anchor: date
) -> None:
    """Not its calendar year.

    Between January and the 6th of April those differ, and an entitlement filed
    under a leave year that has not started yet cannot be found by the screen
    looking for this one's -- the demo would open showing no annual leave at all.
    """
    seed_demo(session, anchor=anchor)

    settings = session.execute(select(Settings)).scalars().one()
    assert settings.leave_year_start == "04-06"

    expected = anchor.year if (anchor.month, anchor.day) >= (4, 6) else anchor.year - 1
    entitlement = session.execute(select(LeaveEntitlement)).scalars().one()
    assert entitlement.year == expected


def test_the_screenshot_anchor_still_seeds_what_it_always_did(
    session: Session,
) -> None:
    """The committed shots are bytes, so this seed cannot drift.

    `tests/snapshot/` compares the rendered screens, which would catch a change
    too -- but only by failing on fourteen files at once and looking like a
    layout regression. This says what actually moved.
    """
    seed_demo(session)

    assert holidays_in(2026) == (
        (date(2026, 4, 6), "Easter Monday"),
        (date(2026, 5, 4), "Early May bank holiday"),
        (date(2026, 5, 25), "Spring bank holiday"),
        (date(2026, 8, 31), "Summer bank holiday"),
        (date(2026, 12, 25), "Christmas Day"),
        (date(2026, 12, 28), "Boxing Day"),
        (date(2027, 1, 1), "New Year's Day"),
        (date(2027, 3, 26), "Good Friday"),
        (date(2027, 3, 29), "Easter Monday"),
    )
    assert max(_worked(session)) == ANCHOR
    assert session.execute(select(ClockEvent)).scalars().all()[-1].action.value == "in"


@pytest.mark.parametrize("anchor", ANCHORS)
def test_a_bank_holiday_is_never_a_day_with_work_on_it(
    session: Session, anchor: date
) -> None:
    """Nobody clocks in on Christmas Day, and the demo should not say they did.

    The seed used to know about three bank holidays, all of them in May and
    August, so every Easter and every Christmas in the span was generated as an
    ordinary working day.
    """
    seed_demo(session, anchor=anchor)

    cached = _cached_holidays(session)
    assert cached, "the demo has to know some bank holidays"
    assert _worked(session) & cached == set()
    assert _absent(session) & cached == set()


def test_christmas_is_drawn_as_a_bank_holiday(session: Session) -> None:
    """The one a reader notices, on a demo opened in the new year."""
    seed_demo(session, anchor=date(2027, 1, 14))

    assert date(2026, 12, 25) in _cached_holidays(session)
    assert date(2026, 12, 25) not in _worked(session)


def test_the_half_day_works_half_a_day(session: Session) -> None:
    """Its morning is booked as annual leave, so its afternoon owes half a day.

    Given a whole contract it drew a day off that also earned nearly four hours
    of flexi and ran to 20:36, past the right-hand edge of the punch strip.
    """
    seed_demo(session)

    half = (
        session.execute(
            select(AbsenceDay.date).where(AbsenceDay.portion != Portion.FULL)
        )
        .scalars()
        .one()
    )
    start, finish = _punches(session, half)
    contracted = timedelta(minutes=DEFAULT_CONTRACTED_MINUTES)

    assert finish - start < contracted * 0.75
    assert finish.time() < DAY_WINDOW_END


@pytest.mark.parametrize(
    ("now", "sessions", "punches"),
    [
        pytest.param(time(7, 0), 0, 0, id="before the arrival it would have seeded"),
        pytest.param(time(10, 0), 1, 1, id="on the clock since the morning"),
        pytest.param(time(13, 0), 1, 2, id="off the clock at lunch"),
        pytest.param(time(15, 0), 2, 3, id="back from lunch and still on"),
    ],
)
def test_the_anchor_day_records_nothing_later_than_now(
    session: Session, now: time, sessions: int, punches: int
) -> None:
    """A demo seeded at nine cannot show a clock-in at twenty past one.

    It did, and `flexi --demo` opened claiming a session that had not started,
    a morning that had not happened, and a clock-out key that answered "That
    clock-out is earlier than the clock-in".
    """
    seed_demo(session, anchor=ANCHOR, now=now)

    readings = _punches(session, ANCHOR)
    fetched = session.execute(select(BankHolidayRefresh.fetched_at)).scalars().one()
    today = session.execute(
        select(WorkSession.id).where(WorkSession.work_date == ANCHOR)
    ).scalars()

    assert len(list(today)) == sessions
    assert len(readings) == punches
    assert all(moment.time() <= now for moment in readings)
    assert fetched.time() <= now


@pytest.mark.parametrize("now", [time(10, 0), time(15, 0)])
def test_the_seeded_open_session_can_be_clocked_out_of(
    session: Session, now: time
) -> None:
    """The first key a stranger presses on the dashboard has to work."""
    seed_demo(session, anchor=ANCHOR, now=now)

    result = build_services(session).clock.clock_out(
        now=datetime.combine(ANCHOR, now, tzinfo=UTC)
    )

    assert result.success, result.message
