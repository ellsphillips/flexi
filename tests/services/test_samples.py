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

from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from flexi.constants import AbsenceType, Portion
from flexi.models.database.db import (
    AbsenceDay,
    ClockEvent,
    LeaveEntitlement,
    Settings,
    WorkSession,
)
from flexi.services import samples
from flexi.services.samples import ANCHOR, holidays_in, seed_demo

ANCHORS = [
    pytest.param(ANCHOR, id="the Thursday the screenshots are taken on"),
    pytest.param(date(2026, 8, 15), id="a Saturday"),
    pytest.param(date(2026, 8, 25), id="the day after a bank holiday"),
    pytest.param(date(2026, 4, 7), id="the second day of a leave year"),
    pytest.param(date(2027, 4, 5), id="the last day of a leave year"),
    pytest.param(date(2028, 2, 29), id="a leap day"),
]


def _worked(session: Session) -> set[date]:
    return set(session.execute(select(WorkSession.work_date)).scalars())


def _absent(session: Session) -> set[date]:
    return set(session.execute(select(AbsenceDay.date)).scalars())


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
    holidays = {when for when, _ in holidays_in(monday.year)}

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

    holidays = {when for when, _ in holidays_in(anchor.year)}
    holidays |= {when for when, _ in holidays_in(anchor.year - 1)}

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
        (date(2026, 5, 4), "Early May bank holiday"),
        (date(2026, 5, 25), "Spring bank holiday"),
        (date(2026, 8, 31), "Summer bank holiday"),
    )
    assert max(_worked(session)) == ANCHOR
    assert session.execute(select(ClockEvent)).scalars().all()[-1].action.value == "in"
