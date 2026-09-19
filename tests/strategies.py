"""Hypothesis strategies for Flexi's domain, in one place.

An example-based test says "on 29 February this happens"; a property says "on
every date this holds". The second needs a vocabulary of *plausible* values —
dates a timesheet could really carry, leave-year starts a person could really
choose — because a strategy that generates year 1 or a 5000-minute working day
finds bugs nobody will ever meet and hides the ones they will.

So the ranges here are the ranges Flexi lives in: dates within a few years of
now, leave years starting on any day of any month, including the 29th of
February, which is the one that used to crash. Shared vocabulary only -- a
strategy one test needs stays in that test, and a strategy nothing needs is
scaffolding around a property nobody wrote.
"""

from __future__ import annotations

from datetime import date, timedelta

from hypothesis import strategies as st

from flexi.constants import Granularity, Portion

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


signed_durations = st.integers(min_value=-40 * 24 * 60, max_value=40 * 24 * 60).map(
    lambda minutes: timedelta(minutes=minutes)
)
"""A flexi balance, which is signed and can be large after a long year."""
