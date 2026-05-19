from __future__ import annotations

from datetime import date, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from flexi.models.database.db import (
    DEFAULT_CONTRACTED_MINUTES,
    DEFAULT_WINDOW_END,
    DEFAULT_WINDOW_START,
    LeaveEntitlement,
    Settings,
)


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
        leave_year_start: str,
        working_days: str,
        bank_holiday_division: str,
        auto_close_time: str,
        contracted_minutes: int | None = None,
        day_window_start: str | None = None,
        day_window_end: str | None = None,
    ) -> Settings:
        # Normalise leave_year_start to MM-DD (accept / or -)
        m, d = parse_month_day(leave_year_start)
        normalised_start = f"{m:02d}-{d:02d}"

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
        minutes = settings.contracted_minutes if settings else DEFAULT_CONTRACTED_MINUTES
        return timedelta(minutes=minutes)

    def get_day_window(self) -> tuple[str, str]:
        """The span of the day the punch strip draws, as ``HH:MM`` strings."""
        settings = self.get_settings()
        if settings is None:
            return DEFAULT_WINDOW_START, DEFAULT_WINDOW_END
        return settings.day_window_start, settings.day_window_end

    def get_working_day_indices(self) -> list[int]:
        """Return weekday indices (0=Monday) for configured working days."""
        settings = self.get_settings()
        if settings is None:
            return [0, 1, 2, 3, 4]
        return [int(d) for d in settings.working_days.split(",")]

    def is_working_day(self, weekday: int) -> bool:
        return weekday in self.get_working_day_indices()

    def get_auto_close_time(self) -> time:
        settings = self.get_settings()
        if settings is None:
            return time(18, 0)
        h, m = settings.auto_close_time.split(":")
        return time(int(h), int(m))

    def get_leave_year_start(self) -> tuple[int, int]:
        """Return (month, day) of leave year start."""
        settings = self.get_settings()
        raw = settings.leave_year_start if settings else "01-01"
        return parse_month_day(raw)

    def active_leave_year(self, ref: date | None = None) -> int:
        """Return the calendar year of the active leave year containing ref."""
        if ref is None:
            ref = date.today()
        m, d = self.get_leave_year_start()
        start_this_year = date(ref.year, m, d)
        return ref.year if ref >= start_this_year else ref.year - 1

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


def parse_month_day(raw: str) -> tuple[int, int]:
    """Parse a MM-DD or MM/DD string into (month, day).

    Raises ValueError if the string is not a valid month-day.
    """
    import re

    m = re.match(r"^(\d{1,2})[/\-](\d{1,2})$", raw.strip())
    if not m:
        msg = f"Invalid date format '{raw}', expected MM-DD or MM/DD"
        raise ValueError(msg)
    month, day = int(m.group(1)), int(m.group(2))
    if not (1 <= month <= 12):
        msg = f"Month {month} out of range 1-12"
        raise ValueError(msg)
    if not (1 <= day <= 31):
        msg = f"Day {day} out of range 1-31"
        raise ValueError(msg)
    return month, day
