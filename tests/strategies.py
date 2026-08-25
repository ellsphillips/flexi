"""Hypothesis strategies for Flexi's domain, in one place.

An example-based test says "on 29 February this happens"; a property says "on
every date this holds". The second needs a vocabulary of *plausible* values —
dates a timesheet could really carry, leave-year starts a person could really
choose — because a strategy that generates year 1 or a 5000-minute working day
finds bugs nobody will ever meet and hides the ones they will.

So the ranges here are the ranges Flexi lives in: dates within a few years of
now, leave years starting on any day of any month (including the 29th of
February, which is the one that used to crash), working weeks that are subsets
of Monday to Sunday. Everything is built from these rather than inline, so a
property test reads as the property and not as its scaffolding.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from hypothesis import strategies as st

from flexi.constants import AbsenceType, Granularity, Portion

EARLIEST = date(2020, 1, 1)
"""Before Flexi existed. Far enough back to cross several leap years."""

LATEST = date(2035, 12, 31)
"""Far enough forward that a leave year booked today ends inside the range."""

dates = st.dates(min_value=EARLIEST, max_value=LATEST)
"""Any date a timesheet could plausibly carry."""

months = st.integers(min_value=1, max_value=12)
days_of_month = st.integers(min_value=1, max_value=31)
"""1-31 regardless of the month: `leaveyear.clamp` exists precisely because a
person may choose the 31st and February may not have one."""

first_weekdays = st.integers(min_value=0, max_value=6)
"""Which day a week is drawn as starting on, Monday=0 as `date.weekday` counts."""

granularities: st.SearchStrategy[Granularity] = st.sampled_from(list(Granularity))
absence_types: st.SearchStrategy[AbsenceType] = st.sampled_from(list(AbsenceType))
portions: st.SearchStrategy[Portion] = st.sampled_from(list(Portion))
"""Annotated: `Granularity` is a `StrEnum`, so `sampled_from` infers `str` and
every property taking one would silently lose its type."""


@st.composite
def year_starts(draw: st.DrawFn) -> tuple[int, int]:
    """A (month, day) leave-year start, including the 29th of February.

    Not `st.tuples(months, days_of_month)` filtered to real dates: the whole
    point is that 31 April and 29 February are choosable, because the settings
    screen lets somebody choose them and the arithmetic has to cope.
    """
    return draw(months), draw(days_of_month)


@st.composite
def date_spans(draw: st.DrawFn, *, max_length: int = 400) -> tuple[date, date]:
    """A start and an end, in order, no longer than a leave year and a bit."""
    start = draw(dates)
    length = draw(st.integers(min_value=0, max_value=max_length))
    return start, min(start + timedelta(days=length), LATEST)


@st.composite
def working_weeks(draw: st.DrawFn) -> frozenset[int]:
    """A non-empty set of weekdays somebody is contracted to work."""
    return frozenset(
        draw(
            st.sets(st.integers(min_value=0, max_value=6), min_size=1, max_size=7),
        )
    )


durations = st.integers(min_value=0, max_value=24 * 60).map(
    lambda minutes: timedelta(minutes=minutes)
)
"""A stretch of a working day, whole minutes.

Whole minutes because that is the resolution Flexi stores and displays; a
strategy generating microseconds would fail an `hm` round-trip for a reason
that is not a bug.
"""

signed_durations = st.integers(min_value=-40 * 24 * 60, max_value=40 * 24 * 60).map(
    lambda minutes: timedelta(minutes=minutes)
)
"""A flexi balance, which is signed and can be large after a long year."""


@st.composite
def moments(draw: st.DrawFn) -> datetime:
    """A naive local wall-clock instant, to the minute.

    Naive on purpose: Flexi records the time on the clock on the wall, and the
    whole `wallclock` module exists to keep it that way.
    """
    day = draw(dates)
    minute = draw(st.integers(min_value=0, max_value=24 * 60 - 1))
    return datetime.combine(day, time(minute // 60, minute % 60))


@st.composite
def sessions(draw: st.DrawFn, *, day: date | None = None) -> tuple[datetime, datetime]:
    """One clock-in and clock-out on the same day, in order."""
    on = day if day is not None else draw(dates)
    start = draw(st.integers(min_value=0, max_value=23 * 60))
    length = draw(st.integers(min_value=1, max_value=24 * 60 - start))
    opened = datetime.combine(on, time(start // 60, start % 60))
    return opened, opened + timedelta(minutes=length)
