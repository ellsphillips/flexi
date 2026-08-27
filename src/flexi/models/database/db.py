from __future__ import annotations

from datetime import date as date_type
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from flexi.constants import AbsenceType, ClockAction, EventSource, Portion
from flexi.models.database.invariants import (
    register_clock_event_immutability as _register_clock_event_immutability,
)
from flexi.models.database.invariants import (
    register_work_session_action_invariants as _register_work_session_action_invariants,
)

__all__ = (
    "DEFAULT_CONTRACTED_MINUTES",
    "DEFAULT_WINDOW_END",
    "DEFAULT_WINDOW_START",
    "SETTINGS_SINGLETON_KEY",
    "AbsenceDay",
    "BalanceAdjustment",
    "BankHolidayCache",
    "BankHolidayRefresh",
    "Base",
    "ClockEvent",
    "LeaveEntitlement",
    "Settings",
    "WorkSession",
)

DEFAULT_CONTRACTED_MINUTES = 444
"""7h24 — the standard day these figures are all measured against.

Minutes rather than hours because 7.4 is not representable in binary floating
point, and a leave year of rounding it produces a balance that disagrees with
the sum of its own rows.
"""

DEFAULT_WINDOW_START = "07:00"
DEFAULT_WINDOW_END = "19:00"
SETTINGS_SINGLETON_KEY = 1
"""The single value accepted by :class:`Settings.singleton_key`.

A constrained constant key turns the application's one-row settings convention
into a database invariant.  The unique constraint limits the table to one row;
the check constraint prevents a second row from choosing a different key.
"""


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
    __table_args__ = (
        CheckConstraint(
            f"singleton_key = {SETTINGS_SINGLETON_KEY}",
            name="ck_settings_singleton_key",
        ),
        UniqueConstraint("singleton_key", name="uq_settings_singleton_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    singleton_key: Mapped[int] = mapped_column(
        Integer(),
        default=SETTINGS_SINGLETON_KEY,
        server_default=text(str(SETTINGS_SINGLETON_KEY)),
    )
    """Constant database key that makes this a true singleton table."""
    leave_year_start: Mapped[str] = mapped_column(String(5))  # "MM-DD"
    working_days: Mapped[str] = mapped_column(String(27))  # "0,1,2,3,4"
    bank_holiday_division: Mapped[str] = mapped_column(String(30))
    auto_close_time: Mapped[str] = mapped_column(String(5))  # "HH:MM"
    contracted_minutes: Mapped[int] = mapped_column(
        Integer(),
        default=DEFAULT_CONTRACTED_MINUTES,
        server_default=str(DEFAULT_CONTRACTED_MINUTES),
    )
    day_window_start: Mapped[str] = mapped_column(
        String(5), default=DEFAULT_WINDOW_START, server_default=DEFAULT_WINDOW_START
    )
    day_window_end: Mapped[str] = mapped_column(
        String(5), default=DEFAULT_WINDOW_END, server_default=DEFAULT_WINDOW_END
    )


class LeaveEntitlement(Base):
    """Per-year leave entitlement with half-day support."""

    __tablename__ = "leave_entitlements"

    id: Mapped[int] = mapped_column(primary_key=True)
    year: Mapped[int] = mapped_column(Integer, unique=True)
    days: Mapped[float] = mapped_column(Float())


class BankHolidayRefresh(Base):
    """A complete cached division calendar, including an empty one.

    Freshness belongs to the response as a whole, not to each event in it.  A
    row therefore records that one division was fetched successfully even when
    GOV.UK returned no events.  The division is the natural key because only
    the latest complete response is retained.
    """

    __tablename__ = "bank_holiday_refreshes"

    division: Mapped[str] = mapped_column(String(30), primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime())
    events: Mapped[list[BankHolidayCache]] = relationship(
        back_populates="refresh",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class BankHolidayCache(Base):
    """One event in a successfully fetched GOV.UK division calendar."""

    __tablename__ = "bank_holiday_cache"
    __table_args__ = (UniqueConstraint("division", "date", name="uq_division_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    division: Mapped[str] = mapped_column(
        ForeignKey(
            "bank_holiday_refreshes.division",
            name="fk_bank_holiday_cache_division_refresh",
            ondelete="CASCADE",
        )
    )
    date: Mapped[date_type] = mapped_column(Date())
    title: Mapped[str] = mapped_column(String(100))
    refresh: Mapped[BankHolidayRefresh] = relationship(back_populates="events")


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
    source: Mapped[EventSource] = mapped_column(
        Enum(
            EventSource,
            native_enum=False,
            create_constraint=False,
            values_callable=lambda members: [member.value for member in members],
            length=20,
        ),
        default=EventSource.USER,
        server_default=EventSource.USER.value,
    )
    """Whether a person punched this or the auto-close sweep did.

    Stored as its value rather than its name, and without a CHECK constraint,
    because migration 0004 wrote a plain `VARCHAR(20)` and 0010 reads the
    column back to decide whose timestamps it may rewrite. `create_constraint`
    is already False by default; it is written down because the default is what
    keeps `create_all` from building a schema the migrations never did."""


_register_clock_event_immutability(ClockEvent.__table__)


class WorkSession(Base):
    """A work session linking a clock-in to an optional clock-out.

    ``work_date`` is the *local* date of the clock-in, so a session that runs
    past midnight belongs to the day it started — which is how a person thinks
    about a late finish, and how a weekly total has to add up.
    """

    __tablename__ = "work_sessions"
    __table_args__ = (
        UniqueConstraint("clock_in_id", name="uq_work_sessions_clock_in_id"),
        UniqueConstraint("clock_out_id", name="uq_work_sessions_clock_out_id"),
        Index(
            "uq_work_sessions_one_open",
            "voided",
            unique=True,
            sqlite_where=text("clock_out_id IS NULL AND voided = 0"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    clock_in_id: Mapped[int] = mapped_column(ForeignKey("clock_events.id"))
    clock_out_id: Mapped[int | None] = mapped_column(
        ForeignKey("clock_events.id"), nullable=True
    )
    work_date: Mapped[date_type] = mapped_column(Date())
    auto_closed: Mapped[bool] = mapped_column(
        Boolean(), default=False, server_default=text("0")
    )
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    voided: Mapped[bool] = mapped_column(Boolean(), default=False, server_default="0")
    """A corrected session. Clock events are immutable, so a correction inserts
    a replacement pair and marks the original voided rather than editing it."""

    clock_in_event: Mapped[ClockEvent] = relationship(foreign_keys=[clock_in_id])
    clock_out_event: Mapped[ClockEvent | None] = relationship(
        foreign_keys=[clock_out_id]
    )


_register_work_session_action_invariants(WorkSession.__table__)


class AbsenceDay(Base):
    """An absence covering a whole day, or one half of one.

    Two half-days of *different* types may share a date — a sick morning and an
    annual afternoon is a real thing that happens. Two partial unique indexes
    treat ``FULL`` as conflicting once with ``AM`` and once with ``PM``. This
    admits the useful ``AM + PM`` pair while making every full/half collision a
    database error, including writes that bypass the service layer.
    """

    __tablename__ = "absence_days"
    __table_args__ = (
        UniqueConstraint("date", "portion", name="uq_date_portion"),
        Index(
            "uq_absence_date_full_am",
            "date",
            unique=True,
            sqlite_where=text("portion IN ('FULL', 'AM')"),
        ),
        Index(
            "uq_absence_date_full_pm",
            "date",
            unique=True,
            sqlite_where=text("portion IN ('FULL', 'PM')"),
        ),
    )

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
