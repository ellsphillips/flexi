from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Optional

import pydantic

if TYPE_CHECKING:
    from flexi.types import Leave


class Day(pydantic.BaseModel):
    """A day of work."""

    date: str
    sessions: list[Session] = pydantic.Field(default_factory=list)
    corrections: list[Correction] = pydantic.Field(default_factory=list)
    leave: Optional[Leave] = None


class Session(pydantic.BaseModel):
    """Continuous work period."""

    clock_in: str
    clock_out: str

    @pydantic.computed_field  # type: ignore # noqa: PGH003
    @property
    def duration(self) -> dt.timedelta:
        """Duration of the session."""
        fmt = "%H:%M"
        clock_out = dt.datetime.strptime(self.clock_out, fmt).astimezone()
        clock_in = dt.datetime.strptime(self.clock_in, fmt).astimezone()
        return clock_out - clock_in


class Correction(pydantic.BaseModel):
    """Correction to a session."""

    message: str
    session: Session


class Log(pydantic.BaseModel):
    """All flexi history for the user."""

    days: list[Day]