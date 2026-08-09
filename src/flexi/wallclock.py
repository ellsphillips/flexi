"""The only place Flexi reads the system clock.

Flexi deals in local civil time — "what day is it where you are" — not an
instant on a global timeline, so both of these are deliberately naive and the
timezone lints are suppressed here rather than everywhere. Routing every read
through one module also leaves a single seam for the demo, the tests, and any
later "show me last March" mode.
"""

from __future__ import annotations

from datetime import date, datetime


def today() -> date:
    """The current local date."""
    return date.today()  # noqa: DTZ011


def now() -> datetime:
    """The current local date and time."""
    return datetime.now()  # noqa: DTZ005
