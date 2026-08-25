from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from flexi import wallclock
from flexi.constants import DEFAULT_DIVISION, Division
from flexi.domain import leaveyear
from flexi.domain.dates import DAY_NAMES, MONTHS_IN_YEAR, weekday_index
from flexi.models.database.db import (
    DEFAULT_CONTRACTED_MINUTES,
    DEFAULT_WINDOW_END,
    DEFAULT_WINDOW_START,
    LeaveEntitlement,
    Settings,
)

LONGEST_MONTH = 31
HOURS_IN_DAY = 24
MINUTES_IN_HOUR = 60
NOON = 12

DEFAULT_AUTO_CLOSE = time(18, 0)
"""When a session somebody forgot to close is closed for them."""

DEFAULT_LEAVE_YEAR_START = "01-01"
"""The calendar year, until somebody says otherwise."""

DEFAULT_ENTITLEMENT_DAYS = 25.0
"""What a year of annual leave is offered as before anybody edits it.

Named because the setup form, the settings screen and the demo data each typed
it out, so the number a new install sees was three numbers that happened to
agree."""


class SettingsService:
    """Read/write application settings and leave entitlements."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ---- settings row ----

    def get_settings(self) -> Settings | None:
        return self._session.execute(select(Settings)).scalar_one_or_none()

    def is_setup_complete(self) -> bool:
        s = self.get_settings()
        if s is None:
            return False
        # All required fields must be present
        return bool(
            s.leave_year_start
            and s.working_days
            and s.bank_holiday_division
            and s.auto_close_time
        )

    def save_settings(
        self,
        *,
        leave_year_start: str,
        working_days: str,
        bank_holiday_division: str,
        auto_close_time: str,
        contracted_minutes: int | None = None,
        day_window_start: str | None = None,
        day_window_end: str | None = None,
    ) -> Settings:
        # Every parseable field is normalised here rather than at the call
        # sites, so nothing unreadable can reach the database whichever screen
        # wrote it. `auto_close_time` was the one that was not, and a stored
        # "6pm" made every later launch die reading it back.
        month, day = parse_month_day(leave_year_start)
        normalised_start = f"{month:02d}-{day:02d}"
        working_days = ",".join(str(i) for i in parse_working_days(working_days))
        hour, minute = parse_clock_time(auto_close_time)
        auto_close_time = f"{hour:02d}:{minute:02d}"

        settings = self.get_settings()
        if settings is None:
            settings = Settings(
                leave_year_start=normalised_start,
                working_days=working_days,
                bank_holiday_division=bank_holiday_division,
                auto_close_time=auto_close_time,
                contracted_minutes=contracted_minutes or DEFAULT_CONTRACTED_MINUTES,
                day_window_start=day_window_start or DEFAULT_WINDOW_START,
                day_window_end=day_window_end or DEFAULT_WINDOW_END,
            )
            self._session.add(settings)
        else:
            settings.leave_year_start = normalised_start
            settings.working_days = working_days
            settings.bank_holiday_division = bank_holiday_division
            settings.auto_close_time = auto_close_time
            if contracted_minutes is not None:
                settings.contracted_minutes = contracted_minutes
            if day_window_start is not None:
                settings.day_window_start = day_window_start
            if day_window_end is not None:
                settings.day_window_end = day_window_end
        self._session.commit()
        return settings

    # ---- helpers ----

    def get_contracted(self) -> timedelta:
        """How long a standard working day is.

        Held as minutes rather than hours: 7.4 is not representable in binary
        floating point, and a leave year of rounding it produces a balance that
        disagrees with the sum of its own rows.
        """
        settings = self.get_settings()
        minutes = (
            settings.contracted_minutes if settings else DEFAULT_CONTRACTED_MINUTES
        )
        return timedelta(minutes=minutes)

    def get_day_window(self) -> tuple[str, str]:
        """The span of the day the punch strip draws, as ``HH:MM`` strings."""
        settings = self.get_settings()
        if settings is None:
            return DEFAULT_WINDOW_START, DEFAULT_WINDOW_END
        return settings.day_window_start, settings.day_window_end

    def get_working_day_indices(self) -> list[int]:
        """Weekday indices (0=Monday) for the configured working days.

        Falls back to Monday-Friday rather than raising. A stored value that
        cannot be read is a settings problem, not a reason to refuse to open
        somebody's time records.
        """
        settings = self.get_settings()
        if settings is None:
            return list(DEFAULT_WORKING_DAYS)
        try:
            return parse_working_days(settings.working_days)
        except ValueError:
            return list(DEFAULT_WORKING_DAYS)

    def is_working_day(self, weekday: int) -> bool:
        return weekday in self.get_working_day_indices()

    def get_auto_close_time(self) -> time:
        """When to close a session nobody closed.

        Falls back rather than raising, for the same reason
        :meth:`get_working_day_indices` does -- and for one more. Values written
        before this was validated are still in databases, and this is read from
        `_open_database` on every command and from `App.on_mount` before a screen
        is drawn. Raising here is not a settings error; it is an application that
        will not open, with no way in to correct the setting.
        """
        settings = self.get_settings()
        if settings is None:
            return DEFAULT_AUTO_CLOSE
        try:
            hour, minute = parse_clock_time(settings.auto_close_time)
        except ValueError:
            return DEFAULT_AUTO_CLOSE
        return time(hour, minute)

    def get_division(self) -> Division:
        """Whose bank holidays apply, or the default before setup has run.

        Typed, and read from here rather than from the settings row directly.
        Four call sites unpacked the row themselves and three of them wrote the
        same ``or DEFAULT_DIVISION.value`` fallback out again; the fourth
        compared a stored slug against a member and never matched.
        """
        settings = self.get_settings()
        if settings is None or not settings.bank_holiday_division:
            return DEFAULT_DIVISION
        try:
            return Division(settings.bank_holiday_division)
        except ValueError:
            # A slug this build does not know is a settings problem, not a
            # reason to refuse to open somebody's records -- the same bargain
            # `get_working_day_indices` and `get_auto_close_time` strike.
            return DEFAULT_DIVISION

    def get_leave_year_start(self) -> tuple[int, int]:
        """Return (month, day) of leave year start."""
        settings = self.get_settings()
        raw = settings.leave_year_start if settings else DEFAULT_LEAVE_YEAR_START
        return parse_month_day(raw)

    def active_leave_year(self, ref: date | None = None) -> int:
        """The calendar year the active leave year is filed under."""
        month, day = self.get_leave_year_start()
        return leaveyear.active_year(ref or wallclock.today(), month, day)

    # ---- entitlements ----

    def get_entitlement(self, year: int) -> LeaveEntitlement | None:
        stmt = select(LeaveEntitlement).where(LeaveEntitlement.year == year)
        return self._session.execute(stmt).scalar_one_or_none()

    def get_active_entitlement_days(self, ref: date | None = None) -> float | None:
        """Return entitlement days for the active leave year, or None."""
        ent = self.get_entitlement(self.active_leave_year(ref))
        return ent.days if ent else None

    def save_entitlement(self, year: int, days: float) -> LeaveEntitlement:
        ent = self.get_entitlement(year)
        if ent is None:
            ent = LeaveEntitlement(year=year, days=days)
            self._session.add(ent)
        else:
            ent.days = days
        self._session.commit()
        return ent

    def all_entitlements(self) -> list[LeaveEntitlement]:
        stmt = select(LeaveEntitlement).order_by(LeaveEntitlement.year)
        return list(self._session.execute(stmt).scalars())


DEFAULT_WORKING_DAYS = (0, 1, 2, 3, 4)


def named_weekday(token: str) -> int:
    """One weekday of a working pattern, by name or by index.

    Raising rather than answering ``None``, because every caller is reading a
    field somebody typed into and the message is the whole of what they get
    back. The names themselves come from `flexi.domain.dates`, which is where
    the rest of the grammar reads them.
    """
    token = token.strip().lower()
    if token.isdigit():
        index = int(token)
        if 0 <= index < len(DAY_NAMES):
            return index
        msg = f"Day {index} is out of range: use 0 (Monday) to 6 (Sunday)"
        raise ValueError(msg)
    found = weekday_index(token)
    if found is None:
        msg = f"'{token}' is not a day: use Mon-Fri, or 0 (Monday) to 6 (Sunday)"
        raise ValueError(msg)
    return found


def parse_working_days(raw: str) -> list[int]:
    """Weekday indices from whatever somebody typed.

    A field labelled "working days" invites ``Mon-Fri`` as readily as
    ``0,1,2,3,4``, so it takes both, and ranges of either. What it will not do
    is accept something it cannot read: this used to be saved unchecked, and the
    application then failed to start on every subsequent launch.

    Examples:
        >>> parse_working_days("0,1,2,3,4")
        [0, 1, 2, 3, 4]
        >>> parse_working_days("Mon-Fri")
        [0, 1, 2, 3, 4]
        >>> parse_working_days("tue, thu")
        [1, 3]
    """
    if not raw.strip():
        msg = "Choose at least one working day"
        raise ValueError(msg)

    days: set[int] = set()
    for chunk in raw.split(","):
        token = chunk.strip()
        if not token:
            continue
        start, separator, end = token.partition("-")
        if separator and end.strip():
            first, last = named_weekday(start), named_weekday(end)
            if first > last:
                msg = f"'{token}' runs backwards"
                raise ValueError(msg)
            days.update(range(first, last + 1))
        else:
            days.add(named_weekday(token))

    if not days:
        msg = "Choose at least one working day"
        raise ValueError(msg)
    return sorted(days)


def format_working_days(indices: Sequence[int]) -> str:
    """The days named, for a label somebody has to read back.

    Examples:
        >>> format_working_days([0, 1, 2, 3, 4])
        'Mon, Tue, Wed, Thu, Fri'
    """
    return ", ".join(DAY_NAMES[index][:3].title() for index in sorted(set(indices)))


_CLOCK = re.compile(r"^(\d{1,2})(?:[:.](\d{1,2}))?\s*([ap]m?)?$", re.IGNORECASE)


def parse_clock_time(raw: str) -> tuple[int, int]:
    """Hour and minute from whatever somebody typed.

    A field labelled "auto-close time" invites `6pm` as readily as `18:00`, so
    it takes both. What it will not do is accept something it cannot read: this
    used to be saved unchecked, and every later launch then died unpacking it.

    Examples:
        >>> parse_clock_time("18:00")
        (18, 0)
        >>> parse_clock_time("6pm")
        (18, 0)
        >>> parse_clock_time("9.30am")
        (9, 30)
        >>> parse_clock_time("18")
        (18, 0)
        >>> parse_clock_time("12am")
        (0, 0)
    """
    found = _CLOCK.match(raw.strip())
    if found is None:
        msg = f"'{raw}' is not a time: use HH:MM, like 18:00"
        raise ValueError(msg)

    hour, minute = int(found.group(1)), int(found.group(2) or 0)
    meridiem = (found.group(3) or "").lower()
    if meridiem:
        if not 1 <= hour <= NOON:
            msg = f"'{raw}' is not a time: {hour} does not take am or pm"
            raise ValueError(msg)
        hour = hour % NOON + (NOON if meridiem.startswith("p") else 0)

    if not 0 <= hour < HOURS_IN_DAY:
        msg = f"Hour {hour} out of range 0-23"
        raise ValueError(msg)
    if not 0 <= minute < MINUTES_IN_HOUR:
        msg = f"Minute {minute} out of range 0-59"
        raise ValueError(msg)
    return hour, minute


def parse_month_day(raw: str) -> tuple[int, int]:
    """Parse a MM-DD or MM/DD string into (month, day).

    Raises ValueError if the string is not a valid month-day.
    """
    m = re.match(r"^(\d{1,2})[/\-](\d{1,2})$", raw.strip())
    if not m:
        msg = f"Invalid date format '{raw}', expected MM-DD or MM/DD"
        raise ValueError(msg)
    month, day = int(m.group(1)), int(m.group(2))
    if not (1 <= month <= MONTHS_IN_YEAR):
        msg = f"Month {month} out of range 1-12"
        raise ValueError(msg)
    if not (1 <= day <= LONGEST_MONTH):
        msg = f"Day {day} out of range 1-31"
        raise ValueError(msg)
    return month, day
