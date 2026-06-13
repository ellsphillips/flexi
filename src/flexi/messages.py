"""Messages that travel between widgets.

One rule: **a module never calls another module's ``rebuild()``.** It posts
:class:`DataChanged` with a scope, the screen invalidates the ledger cache once,
and every module that declared an interest in that scope redraws. The v1 code had
``Home.rebuild()`` call four modules by name, which is why adding a fifth meant
editing a method in a different file.
"""

from __future__ import annotations

from datetime import date
from enum import Flag, auto

from textual.message import Message

from flexi.domain.period import Period


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


class DataChanged(Message):
    """Something was written. Bubbles to the screen, which decides who redraws."""

    def __init__(self, scope: Scope = Scope.ALL) -> None:
        super().__init__()
        self.scope = scope


class PeriodChanged(Message):
    """The temporal view moved to a different span."""

    def __init__(self, period: Period) -> None:
        super().__init__()
        self.period = period


class DateSelected(Message):
    """A single date was picked — from the calendar, or from a table row."""

    def __init__(self, when: date) -> None:
        super().__init__()
        self.date = when


class StatusUpdate(Message):
    """A service said something worth putting in the status bar."""

    def __init__(self, text: str, *, tone: str = "neutral", pill: str = "") -> None:
        super().__init__()
        self.text = text
        self.tone = tone
        self.pill = pill
