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

from flexi.constants import AbsenceType, ClockAction, Portion

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
    contracted_minutes: Mapped[int] = mapped_column(
        Integer(), default=DEFAULT_CONTRACTED_MINUTES, server_default="444"
    )
    day_window_start: Mapped[str] = mapped_column(
        String(5), default=DEFAULT_WINDOW_START, server_default=DEFAULT_WINDOW_START
    )
    day_window_end: Mapped[str] = mapped_column(
        String(5), default=DEFAULT_WINDOW_END, server_default=DEFAULT_WINDOW_END
    )
    tracking_since: Mapped[date_type | None] = mapped_column(Date(), nullable=True)
    """The day setup was answered. Days before it expect no work.

    Stamped once, when the row is first written, and left alone by every later
    save: changing the leave year start moves which days are in the year, not
    which of them Flexi was there for.

    Nullable because a database migrated from before this column may have no
    honest answer -- see ``0011``. ``None`` means every day counts, which is
    what Flexi did before.
    """


class LeaveEntitlement(Base):
    """Per-year leave entitlement with half-day support."""

    __tablename__ = "leave_entitlements"

    id: Mapped[int] = mapped_column(primary_key=True)
    year: Mapped[int] = mapped_column(Integer, unique=True)
    days: Mapped[float] = mapped_column(Float())


class BankHolidayCache(Base):
    """Cached bank holiday entries from GOV.UK."""

    __tablename__ = "bank_holiday_cache"
    __table_args__ = (UniqueConstraint("division", "date", name="uq_division_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    division: Mapped[str] = mapped_column(String(30))
    date: Mapped[date_type] = mapped_column(Date())
    title: Mapped[str] = mapped_column(String(100))
    fetched_at: Mapped[datetime] = mapped_column(DateTime())


class ClockEvent(Base):
    """An immutable clock-in or clock-out event.

    Two columns, one reading. ``timestamp`` is the time on the wall as the
    person read it, naive by design -- SQLite has no timestamp type, so
    ``DateTime(timezone=True)`` was decoration that stored whatever field values
    it was handed and dropped the offset. ``utc_offset_minutes`` is how far that
    wall reading was from UTC, so the instant is the one minus the other.

    Both halves are needed. The wall half is the punch strip, the work date and
    the midday split. The offset half is why 22:00 on 24 October to 06:00 on
    25 October is nine hours and not eight.
    """

    __tablename__ = "clock_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    action: Mapped[ClockAction] = mapped_column(Enum(ClockAction))
    timestamp: Mapped[datetime] = mapped_column(DateTime())
    utc_offset_minutes: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    """Minutes east of UTC when the clock was read; the instant is ``timestamp``
    minus this. ``None`` only on rows Flexi wrote before it recorded one."""
    source: Mapped[str] = mapped_column(String(20), default="user")


class WorkSession(Base):
    """A work session linking a clock-in to an optional clock-out.

    ``work_date`` is the *local* date of the clock-in, so a session that runs
    past midnight belongs to the day it started — which is how a person thinks
    about a late finish, and how a weekly total has to add up.
    """

    __tablename__ = "work_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    clock_in_id: Mapped[int] = mapped_column(ForeignKey("clock_events.id"))
    clock_out_id: Mapped[int | None] = mapped_column(
        ForeignKey("clock_events.id"), nullable=True
    )
    work_date: Mapped[date_type] = mapped_column(Date())
    auto_closed: Mapped[bool] = mapped_column(Boolean(), default=False)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    voided: Mapped[bool] = mapped_column(Boolean(), default=False, server_default="0")
    """A corrected session. Clock events are immutable, so a correction inserts
    a replacement pair and marks the original voided rather than editing it."""

    clock_in_event: Mapped[ClockEvent] = relationship(foreign_keys=[clock_in_id])
    clock_out_event: Mapped[ClockEvent | None] = relationship(
        foreign_keys=[clock_out_id]
    )


class AbsenceDay(Base):
    """An absence covering a whole day, or one half of one.

    Two half-days of *different* types may share a date — a sick morning and an
    annual afternoon is a real thing that happens — so the uniqueness constraint
    is on the pair. A full day cannot coexist with either half; that rule is
    enforced in :class:`~flexi.services.absence.AbsenceService`, because SQLite
    cannot express it as a constraint.
    """

    __tablename__ = "absence_days"
    __table_args__ = (UniqueConstraint("date", "portion", name="uq_date_portion"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[date_type] = mapped_column(Date())
    absence_type: Mapped[AbsenceType] = mapped_column(Enum(AbsenceType))
    portion: Mapped[Portion] = mapped_column(
        Enum(Portion), default=Portion.FULL, server_default="FULL"
    )
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)


class BalanceAdjustment(Base):
    """A signed correction to the flexi balance, with a reason.

    ``date`` is when the correction takes effect: it counts toward any balance
    computed for that date or later. Deleting the records instead would lose the
    audit trail and would not survive the next recomputation.
    """

    __tablename__ = "balance_adjustments"

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[date_type] = mapped_column(Date(), index=True)
    minutes: Mapped[int] = mapped_column(Integer())
    reason: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime())
