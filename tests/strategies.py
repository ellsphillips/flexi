"""Hypothesis strategies for Flexi's domain, in one place.

The ranges are the ranges Flexi lives in: dates within a few years of now,
leave years starting on any day of any month, including 29 February. Shared
vocabulary only; a strategy one test needs stays in that test.
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
"""1-31 regardless of the month: `leaveyear.clamp` exists because the 31st is
choosable and February has no 31st."""

first_weekdays = st.integers(min_value=0, max_value=6)
"""Which day a week is drawn as starting on, Monday=0 as `date.weekday` counts."""

granularities: st.SearchStrategy[Granularity] = st.sampled_from(list(Granularity))
portions: st.SearchStrategy[Portion] = st.sampled_from(list(Portion))
"""Annotated: `Granularity` is a `StrEnum`, so `sampled_from` infers `str` and
every property taking one would silently lose its type."""


@st.composite
def year_starts(draw: st.DrawFn) -> tuple[int, int]:
    """A (month, day) leave-year start, including the 29th of February.

    Unfiltered by the calendar: the settings screen offers 31 April and 29
    February, so the arithmetic has to cope with them.
    """
    return draw(months), draw(days_of_month)


signed_durations = st.integers(min_value=-40 * 24 * 60, max_value=40 * 24 * 60).map(
    lambda minutes: timedelta(minutes=minutes)
)
"""A flexi balance, which is signed and can be large after a long year."""
