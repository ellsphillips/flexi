"""The only place Flexi reads the system clock.

Two readings, and the difference between them is what they are for.
:func:`now` is the wall time somebody lives in -- shown, written to a timesheet,
and pinned to a chosen zone by :func:`pinned`. :func:`utc_now` is an instant
that is only ever stored or compared: a backup's stamp, a cache's age, the row
a correction was written at. Nobody reads one off a screen, so it carries no
zone and takes no pin.

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

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo


class _Pin:
    """The zone every reading is taken in, or ``None`` for the machine's own."""

    zone: tzinfo | None = None


_PIN = _Pin()


@contextmanager
def pinned(zone: tzinfo | None) -> Iterator[None]:
    """Read the clock in a chosen zone for the duration of the block.

    The seam this module's docstring promises, and the suite and
    ``scripts/shoot.py`` are what need it: a snapshot taken on a British laptop
    and one taken on a UTC runner have to be the same bytes, so "local" has to
    be something a test can state rather than something the machine decides.

    ``TZ`` cannot state it. :func:`time.tzset` is POSIX only, so on Windows
    exporting ``TZ`` changes nothing and the pin silently is not one -- which is
    a worse outcome than no pin, because the run still claims the zone in its
    name. This works the same everywhere, because it is the one function that
    reads the zone rather than the environment underneath it.
    """
    previous, _PIN.zone = _PIN.zone, zone
    try:
        yield
    finally:
        _PIN.zone = previous


def now() -> datetime:
    """The current moment: local wall time, carrying the offset in force.

    Read as an instant and then converted, rather than read as a wall time, so
    that the hour which happens twice on the October Sunday resolves to the one
    it actually was. ``datetime.now()`` alone cannot tell them apart.
    """
    return local(datetime.now(tz=UTC))


def utc_now() -> datetime:
    """The current instant, in UTC, for anything stored or compared.

    Separate from :func:`now` because it is never shown. A backup's filename,
    a cache's age and the moment a correction was recorded are all facts about
    when, not about the working day somebody was having, so none of them takes
    the zone the rest of the module exists to pin.

    Here rather than at the four call sites that had it, so that this module's
    first sentence is true.
    """
    return datetime.now(tz=UTC)


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
    zone = _PIN.zone
    if zone is None:
        return moment.astimezone()
    # The same two readings as above, taken against the pinned zone instead of
    # the machine: attaching it to a naive moment resolves an ambiguous hour at
    # `fold=0` and a skipped one to the instant it names, exactly as the
    # platform does with its own.
    anchored = moment.replace(tzinfo=zone) if moment.tzinfo is None else moment
    settled = anchored.astimezone(zone)
    return settled.replace(tzinfo=timezone(settled.utcoffset() or timedelta()))
