from __future__ import annotations

from datetime import date as date_type
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from flexi.constants import AbsenceType, ClockAction

DEFAULT_CONTRACTED_MINUTES = 444
"""7h24 — the standard day these figures are all measured against.

Minutes rather than hours because 7.4 is not representable in binary floating
point, and a leave year of rounding it produces a balance that disagrees with
the sum of its own rows.
"""

DEFAULT_WINDOW_START = "07:00"
DEFAULT_WINDOW_END = "19:00"


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class Settings(Base):
    """Application settings (single-row table).

    Settings are what the balance *depends on* — how long a day is, when the
    leave year turns over, which bank holidays apply. They live in the database
    beside the records they explain. Preferences (keybindings, default period)
    live in ``~/.config/flexi/config.yaml`` instead; see ``flexi/config.py``.
    """

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    leave_year_start: Mapped[str] = mapped_column(String(5))  # "MM-DD"
    working_days: Mapped[str] = mapped_column(String(27))  # "0,1,2,3,4"
    bank_holiday_division: Mapped[str] = mapped_column(String(30))
    auto_close_time: Mapped[str] = mapped_column(String(5))  # "HH:MM"


class LeaveEntitlement(Base):
    """Per-year leave entitlement with half-day support."""

    __tablename__ = "leave_entitlements"

    id: Mapped[int] = mapped_column(primary_key=True)
    year: Mapped[int] = mapped_column(Integer, unique=True)
    days: Mapped[float] = mapped_column(Float())


class BankHolidayCache(Base):
    """Cached bank holiday entries from GOV.UK."""

    __tablename__ = "bank_holiday_cache"
    __table_args__ = (
        UniqueConstraint("division", "date", name="uq_division_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    division: Mapped[str] = mapped_column(String(30))
    date: Mapped[date_type] = mapped_column(Date())
    title: Mapped[str] = mapped_column(String(100))
    fetched_at: Mapped[datetime] = mapped_column(DateTime())
