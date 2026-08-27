"""What travels between widgets, and the flags that decide who redraws.

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
"""

from __future__ import annotations

from datetime import date
from enum import Flag, auto

from textual.message import Message

__all__ = ("DateSelected", "Scope")


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
