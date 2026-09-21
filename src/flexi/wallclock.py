"""The only place Flexi reads the system clock.

:func:`now` is wall time, shown and written to a timesheet, and pinned to a
chosen zone by :func:`pinned`. :func:`utc_now` is an instant only ever stored
or compared, so it carries no zone and takes no pin.

Every moment here is aware and its ``tzinfo`` is a fixed
:class:`datetime.timezone`, never a :class:`zoneinfo.ZoneInfo`. Two datetimes
sharing a ``ZoneInfo`` subtract as wall times and lose any transition between
them; two fixed offsets subtract through UTC.
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

Set only through `pinned`, and context-local so that overlapping async tasks
and threads cannot change one another's reading.
"""


@contextmanager
def pinned(zone: tzinfo | None) -> Iterator[None]:
    """Read the clock in a chosen zone for the duration of the block.

    ``TZ`` cannot do this: :func:`time.tzset` is POSIX only, so on Windows
    exporting ``TZ`` changes nothing. This reads the zone itself, so it holds
    on every platform.
    """
    token = _PINNED_ZONE.set(zone)
    try:
        yield
    finally:
        _PINNED_ZONE.reset(token)


def now() -> datetime:
    """The current moment: local wall time, carrying the offset in force.

    Read as an instant and then converted, so the hour that happens twice on
    the October Sunday resolves to the one it was. ``datetime.now()`` alone
    cannot tell the two apart.
    """
    return local(datetime.now(tz=UTC))


def utc_now() -> datetime:
    """The current instant, in UTC, for anything stored or compared.

    Separate from :func:`now` because it is never shown. A backup's filename, a
    cache's age and the moment a correction was recorded are facts about when,
    not about a working day, so none of them takes a pinned zone.
    """
    return datetime.now(tz=UTC)


def today() -> date:
    """The current local date."""
    return now().date()


def local(moment: datetime) -> datetime:
    """A moment as a local reading with its offset pinned.

    A naive moment is a wall reading on this machine: an ambiguous October hour
    resolves to the first of the two (``fold=0``), and an hour the March Sunday
    skips resolves to the instant it names. An aware moment is converted to
    this machine's reading of that instant. Either way the result carries a
    fixed offset, which is what makes it subtract through UTC.
    """
    zone = _PINNED_ZONE.get()
    if zone is None:
        try:
            return moment.astimezone()
        except (OSError, OverflowError):
            # Windows' `localtime_s` refuses a negative `time_t`, so a moment
            # before 1970 has no reading against the machine's own zone there.
            # The offset in force now stands in for the offset in force then.
            here = timezone(datetime.now().astimezone().utcoffset() or timedelta())
            anchored = moment.replace(tzinfo=here) if moment.tzinfo is None else moment
            return anchored.astimezone(here)
    # The same two readings, taken against the pinned zone instead of the
    # machine, and resolving an ambiguous hour the way the platform does.
    anchored = moment.replace(tzinfo=zone) if moment.tzinfo is None else moment
    settled = anchored.astimezone(zone)
    return settled.replace(tzinfo=timezone(settled.utcoffset() or timedelta()))


def require_aware(moment: datetime, *, name: str = "moment") -> datetime:
    """Return an aware moment, or reject a wall reading with no instant.

    Elapsed-time arithmetic cannot guess which zone a naive reading belongs to,
    least of all in the hour that happens twice. Converting a wall reading
    belongs to :func:`local`; elapsed operations come through here.
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

    A fixed-offset reading carries none of its zone's later rules. Adding in
    UTC and passing the resulting instant through :func:`local` restores the
    offset in force at the destination, on either side of DST.
    """
    instant = require_aware(moment).astimezone(UTC) + duration
    return local(instant)
