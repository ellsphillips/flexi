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
from contextvars import ContextVar
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from typing import Final

__all__ = (
    "advance",
    "elapsed",
    "local",
    "now",
    "pinned",
    "require_aware",
    "today",
    "utc_now",
)

_PINNED_ZONE: Final[ContextVar[tzinfo | None]] = ContextVar(
    "flexi.wallclock.pinned_zone", default=None
)
"""The context-local zone every reading uses, or ``None`` for the machine's.

Private because a public handle on it is a way for anything to move the clock
out from under everything else. `pinned` is the seam; this is where it keeps
its state. Context-local rather than process-global so overlapping async tasks
and threads cannot change one another's reading.
"""


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
    token = _PINNED_ZONE.set(zone)
    try:
        yield
    finally:
        _PINNED_ZONE.reset(token)


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
    zone = _PINNED_ZONE.get()
    if zone is None:
        return moment.astimezone()
    # The same two readings as above, taken against the pinned zone instead of
    # the machine: attaching it to a naive moment resolves an ambiguous hour at
    # `fold=0` and a skipped one to the instant it names, exactly as the
    # platform does with its own.
    anchored = moment.replace(tzinfo=zone) if moment.tzinfo is None else moment
    settled = anchored.astimezone(zone)
    return settled.replace(tzinfo=timezone(settled.utcoffset() or timedelta()))


def require_aware(moment: datetime, *, name: str = "moment") -> datetime:
    """Return an aware moment, or reject a wall reading with no instant.

    Elapsed-time arithmetic cannot guess which zone a naive reading belongs to,
    particularly in the hour that happens twice. Conversion of a deliberate
    wall reading belongs at :func:`local`; elapsed operations use this boundary.
    """
    if moment.tzinfo is None or moment.utcoffset() is None:
        msg = f"{name} must be timezone-aware"
        raise ValueError(msg)
    return moment


def elapsed(start: datetime, end: datetime) -> timedelta:
    """Real elapsed time between two aware moments.

    Converting each end to UTC avoids Python's wall-time subtraction rule for
    two datetimes carrying the same ``ZoneInfo`` object across a transition.
    """
    beginning = require_aware(start, name="start")
    finish = require_aware(end, name="end")
    return finish.astimezone(UTC) - beginning.astimezone(UTC)


def advance(moment: datetime, duration: timedelta) -> datetime:
    """Advance an aware moment by elapsed time and return its new local reading.

    A fixed-offset reading deliberately forgets the zone's future rules. Adding
    in UTC and passing the resulting instant through :func:`local` restores the
    offset in force at the destination, including either side of DST.
    """
    instant = require_aware(moment).astimezone(UTC) + duration
    return local(instant)
