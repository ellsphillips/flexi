"""The only place Flexi reads the system clock.

A moment here is *aware*, and its ``tzinfo`` is always a fixed
:class:`datetime.timezone` -- never a :class:`zoneinfo.ZoneInfo`. That is the
whole design. Two datetimes sharing a ``ZoneInfo`` object subtract as wall
times and silently lose a transition: 22:00 BST to 06:00 GMT comes out at eight
hours instead of nine, and every test that does not span a transition passes.
Two fixed offsets subtract through UTC and give the real elapsed time, while
``.hour``, ``.date()`` and ``strftime`` still read as the person lived them.

Routing every read through one module also leaves a single seam for the demo,
the tests, and any later "show me last March" mode.
"""

from __future__ import annotations

from datetime import UTC, date, datetime


def now() -> datetime:
    """The current moment: local wall time, carrying the offset in force.

    Read as an instant and then converted, rather than read as a wall time, so
    that the hour which happens twice on the October Sunday resolves to the one
    it actually was. ``datetime.now()`` alone cannot tell them apart.
    """
    return datetime.now(tz=UTC).astimezone()


def today() -> date:
    """The current local date."""
    return now().date()


def local(moment: datetime) -> datetime:
    """A moment as a local reading with its offset pinned.

    Naive in, and it is taken as a wall reading on this machine -- which is what
    every time Flexi *manufactures* is: an auto-close stamp, the end of a day,
    the edges of the punch strip. On the October Sunday an ambiguous reading
    resolves to the first of the two (``fold=0``); a reading the March Sunday
    skips resolves to the instant it names.

    Aware in, and it is converted to this machine's reading of that instant --
    so a UTC instant handed in on the October Sunday resolves to whichever of
    the two 01:30s it actually was.

    Either way the result carries a fixed offset rather than a zone, which is
    what makes it subtract through UTC instead of as wall time.
    """
    return moment.astimezone()
