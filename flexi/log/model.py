from __future__ import annotations

import datetime as dt
from typing import Optional, TypedDict

import pydantic

from flexi.types import Leave


def format_clock_time(
    time: str, day: Optional[dt.date] = None, fmt: str = "%H:%M"
) -> dt.datetime:
    """Format a datetime object as a clock time."""
    if day is None:
        day = dt.datetime.today()  # noqa: DTZ002

    return dt.datetime.combine(
        day, dt.datetime.strptime(time, fmt).time()  # noqa: DTZ007
    )


class TDay(TypedDict):
    """A day of work."""

    date: str
    sessions: list[Session]
    corrections: list[Correction]
    leave: Optional[Leave]


class Day(pydantic.BaseModel):
    """A day of work."""

    date: str
    sessions: list[Session] = pydantic.Field(default_factory=list)
    corrections: list[Correction] = pydantic.Field(default_factory=list)
    leave: Optional[Leave] = None

    model_config = pydantic.ConfigDict(
        validate_assignment=True,
        use_enum_values=True,
    )

    @pydantic.model_validator(mode="before")
    @classmethod
    def clear_sessions_if_on_leave(cls, values: TDay) -> TDay:
        """Update the updated_at field."""
        leave = values.get("leave")
        if leave is not None:
            values["sessions"] = []
        return values

    @pydantic.field_validator("sessions")
    @classmethod
    def validate_sessions(cls, v: list[Session]) -> list[Session]:
        """Validate sessions."""
        return v

    @pydantic.field_validator("leave")
    @classmethod
    def validate_leave(cls, v: Leave) -> Leave:
        """Validate sessions."""
        return v


class Session(pydantic.BaseModel):
    """Continuous work period."""

    clock_in: str
    clock_out: str

    @property
    def duration(self) -> dt.timedelta:
        """Duration of the session."""
        if not all([self.clock_in, self.clock_out]):
            return dt.timedelta()

        clock_out = format_clock_time(self.clock_out)
        clock_in = format_clock_time(self.clock_in)

        return clock_out - clock_in


class Correction(pydantic.BaseModel):
    """Correction to a session."""

    message: str
    session: Session


class Log(pydantic.BaseModel):
    """All flexi history for the user."""

    days: list[Day]

    def __getitem__(self, date: str) -> Day:
        """Get a day by date."""
        for day in self.days:
            if day.date == date:
                return day

        self.days.append(Day(date=date))

        return self[date]
