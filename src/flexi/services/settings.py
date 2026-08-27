from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, time, timedelta

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from flexi import wallclock
from flexi.constants import DEFAULT_DIVISION, Division
from flexi.domain import leaveyear
from flexi.domain.dates import DAY_NAMES, MONTHS_IN_YEAR, weekday_index
from flexi.domain.punch import Window
from flexi.models.database.db import (
    DEFAULT_CONTRACTED_MINUTES,
    DEFAULT_WINDOW_END,
    DEFAULT_WINDOW_START,
    LeaveEntitlement,
    Settings,
)

__all__ = (
    "CLOCK_PATTERN",
    "DEFAULT_AUTO_CLOSE",
    "DEFAULT_ENTITLEMENT_DAYS",
    "DEFAULT_LEAVE_YEAR_START",
    "DEFAULT_WORKING_DAYS",
    "HOURS_IN_DAY",
    "LONGEST_MONTH",
    "MINUTES_IN_HOUR",
    "NOON",
    "LeaveYearStart",
    "ResolvedSettings",
    "SettingsService",
    "SettingsUpdate",
    "WorkingDays",
    "duration_minutes",
    "format_clock_time",
    "format_leave_year_start",
    "format_window",
    "format_working_days",
    "named_weekday",
    "parse_clock_time",
    "parse_month_day",
    "parse_settings",
    "parse_working_days",
    "read_or",
    "readable_window",
    "resolve_settings",
    "validate_window",
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

type LeaveYearStart = tuple[int, int]
"""The month and day on which a leave year begins."""

type WorkingDays = tuple[int, ...]
"""Ordered weekday indices, where Monday is zero and Sunday is six."""


@dataclass(frozen=True, kw_only=True, slots=True)
class SettingsUpdate:
    """A typed settings write, expressed entirely in domain values.

    ``contracted`` and ``day_window`` are updates rather than nullable stored
    values. Omitting either preserves the value already persisted; on the first
    write, the database defaults are used. A zero contracted duration remains
    an explicit update because absence is represented only by ``None``.
    """

    leave_year_start: LeaveYearStart
    working_days: WorkingDays
    division: Division
    auto_close: time
    contracted: timedelta | None = None
    day_window: Window | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class ResolvedSettings:
    """The settings row, read once, with every fallback already applied.

    The six accessors below each opened by selecting the one-row settings
    table and each wrote its own "or the default" clause. Drawing a wallet took
    ten of those selects, and two of the six had drifted from the rule the
    other four state: a stored value that cannot be read is a settings problem,
    not a reason to refuse to open somebody's time records.

    One read, one place the fallbacks live, and a value the hot paths can take
    once and pass down.
    """

    contracted: timedelta
    day_window: Window
    working_days: WorkingDays
    auto_close: time
    division: Division
    leave_year_start: LeaveYearStart


def resolve_settings(settings: Settings | None) -> ResolvedSettings:
    """Resolve one stored row, or the complete set of application defaults."""
    default_window = Window.parse(DEFAULT_WINDOW_START, DEFAULT_WINDOW_END)
    if settings is None:
        return ResolvedSettings(
            contracted=timedelta(minutes=DEFAULT_CONTRACTED_MINUTES),
            day_window=default_window,
            working_days=DEFAULT_WORKING_DAYS,
            auto_close=DEFAULT_AUTO_CLOSE,
            division=DEFAULT_DIVISION,
            leave_year_start=parse_month_day(DEFAULT_LEAVE_YEAR_START),
        )
    return ResolvedSettings(
        contracted=timedelta(minutes=settings.contracted_minutes),
        day_window=read_or(
            lambda: readable_window(settings.day_window_start, settings.day_window_end),
            default_window,
        ),
        working_days=read_or(
            lambda: tuple(parse_working_days(settings.working_days)),
            DEFAULT_WORKING_DAYS,
        ),
        auto_close=read_or(
            lambda: time(*parse_clock_time(settings.auto_close_time)),
            DEFAULT_AUTO_CLOSE,
        ),
        division=read_or(
            lambda: Division(settings.bank_holiday_division), DEFAULT_DIVISION
        ),
        leave_year_start=read_or(
            lambda: parse_month_day(settings.leave_year_start),
            parse_month_day(DEFAULT_LEAVE_YEAR_START),
        ),
    )


def readable_window(start: str, end: str) -> Window:
    """The stored day window, checked before anything tries to draw in it.

    Older databases may contain an unreadable or backwards pair. Refusing it
    here lets :func:`resolve_settings` choose the safe default before a widget
    tries to draw the window inside Textual's render loop.
    """
    return validate_window(
        Window(time(*parse_clock_time(start)), time(*parse_clock_time(end)))
    )


def read_or[T](value: Callable[[], T], fallback: T) -> T:
    """A stored field, or the default when it cannot be read.

    The bargain the module strikes, in one place rather than in four of the six
    accessors that were supposed to be striking it. A value written by an older
    version, or by hand, must not be an application that will not open — there
    would be no way in to correct the setting.
    """
    try:
        return value()
    except ValueError:
        return fallback


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

    def save_settings(self, update: SettingsUpdate) -> Settings:
        """Persist and commit one typed settings update."""
        try:
            settings = self.stage_settings(update)
            self._session.commit()
        except SQLAlchemyError:
            self._session.rollback()
            raise
        return settings

    def stage_settings(self, update: SettingsUpdate) -> Settings:
        """Apply a typed update to this transaction without committing it.

        This is the composable persistence primitive used when settings and
        entitlements must succeed or fail together. Application callers will
        normally prefer :meth:`save_settings` or
        :meth:`save_settings_and_entitlements`.
        """
        leave_year_start = format_leave_year_start(update.leave_year_start)
        working_days = format_working_days(update.working_days)
        division = update.division.value
        auto_close = format_clock_time(update.auto_close)
        contracted_minutes = (
            duration_minutes(update.contracted)
            if update.contracted is not None
            else None
        )
        day_window = (
            format_window(update.day_window) if update.day_window is not None else None
        )
        settings = self.get_settings()
        if settings is None:
            settings = Settings(
                leave_year_start=leave_year_start,
                working_days=working_days,
                bank_holiday_division=division,
                auto_close_time=auto_close,
                contracted_minutes=(
                    contracted_minutes
                    if contracted_minutes is not None
                    else DEFAULT_CONTRACTED_MINUTES
                ),
                day_window_start=(
                    day_window[0] if day_window is not None else DEFAULT_WINDOW_START
                ),
                day_window_end=(
                    day_window[1] if day_window is not None else DEFAULT_WINDOW_END
                ),
            )
            self._session.add(settings)
        else:
            settings.leave_year_start = leave_year_start
            settings.working_days = working_days
            settings.bank_holiday_division = division
            settings.auto_close_time = auto_close
            if contracted_minutes is not None:
                settings.contracted_minutes = contracted_minutes
            if day_window is not None:
                settings.day_window_start, settings.day_window_end = day_window
        return settings

    def save_settings_and_entitlements(
        self, update: SettingsUpdate, entitlements: Mapping[int, float]
    ) -> Settings:
        """Commit settings and their entitlement edits as one transaction."""
        try:
            settings = self.stage_settings(update)
            for year, days in entitlements.items():
                self.stage_entitlement(year, days)
            self._session.commit()
        except SQLAlchemyError:
            self._session.rollback()
            raise
        return settings

    # ---- helpers ----

    def resolved(self) -> ResolvedSettings:
        """Every setting, in one read, with the fallbacks already applied.

        What a caller that needs more than one of them should ask for. The
        accessors below are for the callers that need exactly one.
        """
        return resolve_settings(self.get_settings())

    def get_contracted(self) -> timedelta:
        """How long a standard working day is.

        Held as minutes rather than hours: 7.4 is not representable in binary
        floating point, and a leave year of rounding it produces a balance that
        disagrees with the sum of its own rows.
        """
        return self.resolved().contracted

    def get_day_window(self) -> Window:
        """The span of the day the punch strip draws."""
        return self.resolved().day_window

    def get_working_day_indices(self) -> list[int]:
        """Weekday indices (0=Monday) for the configured working days."""
        return list(self.resolved().working_days)

    def get_auto_close_time(self) -> time:
        """When to close a session nobody closed."""
        return self.resolved().auto_close

    def get_division(self) -> Division:
        """Whose bank holidays apply, or the default before setup has run."""
        return self.resolved().division

    def get_leave_year_start(self) -> tuple[int, int]:
        """The month and day a leave year begins on."""
        return self.resolved().leave_year_start

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
        try:
            ent = self.stage_entitlement(year, days)
            self._session.commit()
        except SQLAlchemyError:
            self._session.rollback()
            raise
        return ent

    def stage_entitlement(self, year: int, days: float) -> LeaveEntitlement:
        """Apply one entitlement to this transaction without committing it."""
        ent = self.get_entitlement(year)
        if ent is None:
            ent = LeaveEntitlement(year=year, days=days)
            self._session.add(ent)
        else:
            ent.days = days
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


CLOCK_PATTERN = re.compile(r"^(\d{1,2})(?:[:.](\d{1,2}))?\s*([ap]m?)?$", re.IGNORECASE)


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
    found = CLOCK_PATTERN.match(raw.strip())
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


def format_leave_year_start(start: LeaveYearStart) -> str:
    """Serialise a typed leave-year boundary as canonical ``MM-DD``."""
    month, day = start
    validated_month, validated_day = parse_month_day(f"{month}-{day}")
    return f"{validated_month:02d}-{validated_day:02d}"


def format_working_days(days: WorkingDays) -> str:
    """Serialise weekday indices in canonical ascending order."""
    if not days:
        msg = "Choose at least one working day"
        raise ValueError(msg)
    normalised = tuple(sorted(set(days)))
    for day in normalised:
        named_weekday(str(day))
    return ",".join(str(day) for day in normalised)


def format_clock_time(value: time) -> str:
    """Serialise a clock time at the database's minute precision."""
    if value.second or value.microsecond:
        msg = "Clock times must use whole minutes"
        raise ValueError(msg)
    return value.strftime("%H:%M")


def duration_minutes(value: timedelta) -> int:
    """Serialise a non-negative duration without losing sub-minute data."""
    if value < timedelta(0):
        msg = "Contracted duration cannot be negative"
        raise ValueError(msg)
    minutes, remainder = divmod(value, timedelta(minutes=1))
    if remainder:
        msg = "Contracted duration must use whole minutes"
        raise ValueError(msg)
    return minutes


def validate_window(window: Window) -> Window:
    """Return a minute-precise window whose end follows its start."""
    format_clock_time(window.start)
    format_clock_time(window.end)
    if window.end <= window.start:
        msg = "Day window end must be after its start"
        raise ValueError(msg)
    return window


def format_window(window: Window) -> tuple[str, str]:
    """Serialise a validated window as canonical ``HH:MM`` endpoints."""
    validated = validate_window(window)
    return format_clock_time(validated.start), format_clock_time(validated.end)


def parse_settings(
    *,
    leave_year_start: str,
    working_days: str,
    bank_holiday_division: str,
    auto_close_time: str,
    contracted_minutes: int | None = None,
    day_window_start: str | None = None,
    day_window_end: str | None = None,
) -> SettingsUpdate:
    """Parse raw form or CLI values into one immutable settings update.

    The persistence service never accepts strings. This function is the one
    boundary at which permissive human input such as ``Mon-Fri`` and ``6pm``
    is interpreted and normalised. A day window is one value, so its two raw
    endpoints must either both be supplied or both be omitted.
    """
    if (day_window_start is None) != (day_window_end is None):
        msg = "Day window start and end must be provided together"
        raise ValueError(msg)

    window = (
        readable_window(day_window_start, day_window_end)
        if day_window_start is not None and day_window_end is not None
        else None
    )
    return SettingsUpdate(
        leave_year_start=parse_month_day(leave_year_start),
        working_days=tuple(parse_working_days(working_days)),
        division=Division(bank_holiday_division),
        auto_close=time(*parse_clock_time(auto_close_time)),
        contracted=(
            timedelta(minutes=contracted_minutes)
            if contracted_minutes is not None
            else None
        ),
        day_window=window,
    )
