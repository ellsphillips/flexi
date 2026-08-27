"""What travels through Textual, and the flags that decide who redraws.

One rule: a module never calls another module's ``rebuild()``. A module that
wants something written posts a message the *screen* handles -- `BookHere`,
`DeleteHere`, `BookRequested` -- and the screen reports the result, invalidates
the ledger cache once, and calls ``refresh_modules(scope)``. Each module
declares in ``WATCHES`` which scopes it cares about, so clocking in does not
rebuild the calendar's bank-holiday markers, and adding a module is a
declaration rather than an edit to somebody else's method.

There was a generic `DataChanged` message for the same job, and nothing in the
application ever posted one: every write in Flexi goes through a screen, which
is a better rule than the one it was there to allow. Its handler and the
`Module.announce` that would have fed it went with it.

The application-level completion message carries an untrusted network payload
back to Textual's owning loop. It deliberately carries no service or database
object: persistence is resolved only after dispatch.
"""

from __future__ import annotations

from datetime import date
from enum import Flag, auto

from textual.message import Message

__all__ = ("BankHolidayRefreshCompleted", "DateSelected", "Scope")


class Scope(Flag):
    """What changed, so only the widgets that care redraw."""

    NONE = 0
    CLOCK = auto()
    """A session was opened, closed or corrected."""
    ABSENCE = auto()
    """Something was booked, changed or removed."""
    SETTINGS = auto()
    """Contracted hours, the leave year, the working pattern or the division."""
    PERIOD = auto()
    """The temporal view moved. No rows changed."""

    ALL = CLOCK | ABSENCE | SETTINGS | PERIOD


class DateSelected(Message):
    """A single date was picked — from the calendar, or from a table row."""

    def __init__(self, when: date) -> None:
        super().__init__()
        self.date = when


class BankHolidayRefreshCompleted(Message):
    """A worker finished the network-only half of a calendar refresh.

    The payload remains deliberately untrusted. The application receives this
    message on Textual's message loop and asks ``BankHolidayService`` to
    validate and persist it there, so neither a SQLAlchemy session nor an
    engine lease ever crosses the thread boundary.
    """

    payload: object
    forced: bool

    def __init__(self, payload: object, *, forced: bool) -> None:
        super().__init__()
        self.payload = payload
        self.forced = forced
