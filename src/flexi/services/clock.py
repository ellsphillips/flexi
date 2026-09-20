from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from flexi import wallclock
from flexi.constants import EventSource, Portion
from flexi.domain.format import hm, long_date, short_date, spoken
from flexi.domain.ledger import MIDDAY_HOUR, Segment
from flexi.models.database.db import AbsenceDay, WorkSession
from flexi.models.database.moment import moment_of
from flexi.services.absence import covers_the_whole_day
from flexi.services.bank_holidays import BankHolidayService
from flexi.services.ledger import end_of_day, segment_of
from flexi.services.settings import SettingsService
from flexi.services.startup import close_stale_sessions
from flexi.services.transactions import write_transaction
from flexi.services.work_sessions import (
    first_absence_overlap,
    sessions_touching,
    stage_clock_in,
    stage_clock_out,
    stage_correction,
)

__all__ = (
    "CORRECTION_BACKWARDS",
    "CORRECTION_BOOKED",
    "CORRECTION_EMPTY",
    "CORRECTION_FUTURE",
    "CORRECTION_OVERLAP",
    "ClockResult",
    "ClockService",
    "overlapping",
)


@dataclass(frozen=True)
class ClockResult:
    """Result of a clock action."""

    success: bool
    message: str
    warning: str | None = None
    session: WorkSession | None = None
    at: datetime | None = None
    """The moment recorded, on the two returns that record one.

    The status bar stamps a clock message with it, and its presence is what
    says the message can be stamped."""


class ClockService:
    """Atomic clock-in / clock-out operations."""

    def __init__(
        self,
        session: Session,
        settings: SettingsService,
        holidays: BankHolidayService,
        minimum_session: timedelta,
    ) -> None:
        """Every collaborator is required, and none of them has a default.

        A default division or minimum session here would disagree with the
        values the registry hands every other surface.
        """
        self._session = session
        self._settings = settings
        self._holidays = holidays
        self._minimum = minimum_session

    def get_open_session(self) -> WorkSession | None:
        """Return the currently open work session, or None."""
        stmt = select(WorkSession).where(
            WorkSession.clock_out_id.is_(None),
            WorkSession.voided.is_(False),
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def is_clocked_in(self) -> bool:
        return self.get_open_session() is not None

    def _booked_over(
        self, work_date: date, opened_at: datetime, closed_at: datetime
    ) -> Portion | None:
        """Which booked portion a stretch of work collides with, or ``None``.

        The inverse of `DayFacts.has_work_in`, the rule the booking side runs
        on, so the two cannot disagree: a morning booked off and then worked is
        a day paid for twice. Both routes into a work session ask it, `clock_in`
        and `correct`. One date can carry two booked portions, a sick morning
        and an annual afternoon, so they come back as a list.
        """
        stmt = select(AbsenceDay.portion).where(AbsenceDay.date == work_date)
        booked = list(self._session.execute(stmt).scalars().all())
        if covers_the_whole_day(booked):
            return Portion.FULL
        midday = wallclock.local(datetime.combine(work_date, time(MIDDAY_HOUR, 0)))
        if Portion.AM in booked and opened_at < midday:
            return Portion.AM
        if Portion.PM in booked and (opened_at >= midday or closed_at > midday):
            return Portion.PM
        return None

    def sweep(self) -> None:
        """Close work left running on an earlier day.

        The minimum-session preference applies at clock-out, where the person
        sees the decision. Reapplying today's preference to historical rows
        would reinterpret real work on every launch after a config change, so
        startup closes stale sessions and nothing else.
        """
        close_stale_sessions(self._session, self._settings.get_auto_close_time())

    def clock_in(
        self,
        *,
        now: datetime | None = None,
        source: EventSource = EventSource.USER,
    ) -> ClockResult:
        """Clock in. Rejects duplicate clock-in without creating audit rows."""
        self.sweep()
        with write_transaction(self._session):
            # The refusal carries the session, so the caller need not ask for
            # what is already in hand.
            running = self.get_open_session()
            if running is not None:
                return ClockResult(
                    success=False, message="Already clocked in", session=running
                )

            moment = wallclock.local(now) if now is not None else wallclock.now()
            work_date = moment.date()

            # A known bank holiday blocks clocking in; an unknown calendar
            # answers `None` here and does not.
            if self._holidays.holiday_on(work_date) is not None:
                return ClockResult(
                    success=False, message="Cannot clock in on a bank holiday"
                )

            # The moment itself, not the rest of the day: a booked morning
            # leaves the afternoon workable.
            booked = self._booked_over(work_date, moment, moment)
            if booked is Portion.FULL:
                return ClockResult(
                    success=False, message="Cannot clock in on an absence day"
                )
            if booked is not None:
                return ClockResult(
                    success=False,
                    message=f"Cannot clock in during a booked {booked.noun}",
                )

            work_session = stage_clock_in(
                self._session,
                moment,
                work_date,
                source=source,
            )
            if work_session is None:
                return ClockResult(success=False, message="Already clocked in")

        return ClockResult(
            success=True,
            message="Clocked in",
            session=work_session,
            at=moment,
        )

    def clock_out(
        self,
        *,
        now: datetime | None = None,
        source: EventSource = EventSource.USER,
    ) -> ClockResult:
        """Clock out. Rejects clock-out without an open session."""
        with write_transaction(self._session):
            open_session = self.get_open_session()
            if open_session is None:
                return ClockResult(success=False, message="Not clocked in")

            moment = wallclock.local(now) if now is not None else wallclock.now()
            opened = moment_of(open_session.clock_in_event)
            length = moment - opened

            # A future-dated session left by a wrong clock can be discarded;
            # a same-day backwards reading must leave its real work intact.
            ahead = open_session.work_date > moment.date()
            if length < timedelta() and not ahead:
                return ClockResult(
                    success=False,
                    message="That clock-out is earlier than the clock-in",
                    session=open_session,
                )

            short = length < self._minimum
            clash = (
                None if short else first_absence_overlap(self._session, opened, moment)
            )
            if clash is not None:
                booking, _boundary = clash
                return ClockResult(
                    success=False,
                    message=(
                        f"Work overlaps the booked {booking.portion.noun} on "
                        f"{short_date(booking.date)}; remove that absence, "
                        "then clock out again"
                    ),
                    session=open_session,
                )

            closed = stage_clock_out(
                self._session,
                open_session.id,
                moment,
                source=source,
                voided=short,
            )
        if not closed:
            return ClockResult(success=False, message="Not clocked in")

        # Hours on a session dated in the future came off a wrong clock, so
        # they are discarded. The events stay; the day goes back on as a
        # correction.
        if ahead:
            return ClockResult(
                success=True,
                message=(
                    "Discarded — that session is dated "
                    f"{long_date(open_session.work_date)}, which is still to come"
                ),
                session=open_session,
            )

        # Clocking in and straight back out is a slip of the finger. The events
        # stay, being immutable; the session is voided and drops out of the
        # records table and every figure derived from it.
        if short:
            return ClockResult(
                success=True,
                message=f"Discarded — under {spoken(self._minimum)} on the clock",
                session=open_session,
            )
        return ClockResult(
            success=True,
            message="Clocked out",
            session=open_session,
            at=moment,
        )

    # --- corrections ------------------------------------------------------

    def correct(
        self,
        day: date,
        opened: time,
        closed: time,
        *,
        now: date | None = None,
    ) -> ClockResult:
        """Record work on a day that was not clocked at the time.

        Refused when it overlaps a stretch already recorded: two stretches
        sharing an hour count it twice, and no merge rule beats a person looking
        at both.

        A portion already booked off is refused for the same reason: the day
        would be paid for twice, once out of the leave balance and once into the
        flexi balance. The other half of it stays correctable, as `clock_in`
        leaves it.

        A stretch ending after now is a plan, and is refused.

        A bank holiday is *not* refused, though `clock_in` refuses one: a
        correction is the only way to record work that happened on one, and it
        spends no allowance.
        """
        moment = wallclock.now()
        today = now or moment.date()
        if day > today:
            return ClockResult(success=False, message=CORRECTION_FUTURE)

        # Localised before they are measured: the hour the clocks skip in March
        # holds no instants, so on that Sunday 01:00 and 02:00 name the same
        # instant and the span between them is nothing.
        opened_at = wallclock.local(datetime.combine(day, opened))
        closed_at = wallclock.local(datetime.combine(day, closed))
        span = wallclock.elapsed(opened_at, closed_at)
        if span < timedelta():
            return ClockResult(success=False, message=CORRECTION_BACKWARDS)
        if span == timedelta():
            return ClockResult(success=False, message=CORRECTION_EMPTY)

        with write_transaction(self._session):
            booked = self._booked_over(day, opened_at, closed_at)
            if booked is Portion.FULL:
                return ClockResult(
                    success=False,
                    message=CORRECTION_BOOKED.format(day=short_date(day)),
                )
            if booked is not None:
                half = f"{booked.noun} of {short_date(day)}"
                return ClockResult(
                    success=False, message=f"The {half} is already booked off"
                )
            if any(
                overlapping(existing, opened_at, closed_at)
                for existing in self._segments_touching(day)
            ):
                return ClockResult(
                    success=False,
                    message=CORRECTION_OVERLAP.format(day=short_date(day)),
                )
            # Last of the refusals: where a running session and the clock both
            # object, naming the session is the more useful answer.
            if closed_at > moment:
                return ClockResult(
                    success=False, message="A correction cannot run past now"
                )
            recorded = stage_correction(self._session, opened_at, closed_at, day)

        return ClockResult(
            success=True,
            message=f"Recorded {hm(span)} on {short_date(day)}",
            session=recorded,
            at=opened_at,
        )

    def corrections_between(self, start: date, end: date) -> list[Segment]:
        """Every corrected stretch in a span, earliest first.

        Corrections only: a punched session on the same day is not part of a
        review of what was typed in.
        """
        stmt = (
            select(WorkSession)
            .where(
                WorkSession.work_date >= start,
                WorkSession.work_date <= end,
                WorkSession.voided.is_(False),
            )
            .options(
                selectinload(WorkSession.clock_in_event),
                selectinload(WorkSession.clock_out_event),
            )
            .order_by(WorkSession.work_date, WorkSession.id)
        )
        found = (segment_of(row) for row in self._session.scalars(stmt))
        return [segment for segment in found if segment.amended]

    def segments_on(self, day: date) -> list[Segment]:
        """Every stretch already recorded on a date, punched or corrected."""
        return self._segments_dated(day, day)

    def _segments_touching(self, day: date) -> list[Segment]:
        """Every stretch that can claim time on a date, whenever it opened.

        A session belongs to the day it started on, so one running from ten on
        Monday night to two on Tuesday morning is dated Monday and is still two
        hours of Tuesday. `overlapping` compares real instants, so a Monday that
        ended on Monday cannot collide here.
        """
        return [segment_of(row) for row in sessions_touching(self._session, day, day)]

    def _segments_dated(self, start: date, end: date) -> list[Segment]:
        stmt = (
            select(WorkSession)
            .where(
                WorkSession.work_date >= start,
                WorkSession.work_date <= end,
                WorkSession.voided.is_(False),
            )
            .options(
                selectinload(WorkSession.clock_in_event),
                selectinload(WorkSession.clock_out_event),
            )
        )
        return [segment_of(row) for row in self._session.scalars(stmt)]


CORRECTION_BACKWARDS = "That correction ends before it starts"
CORRECTION_BOOKED = "{day} is already booked off in full"
CORRECTION_EMPTY = "A correction has to cover some time"
CORRECTION_FUTURE = "A day that has not happened cannot be corrected"
CORRECTION_OVERLAP = "That overlaps work already recorded on {day}"
"""Formatted with an already-rendered date, not with the `date` itself.

`%-d` is a glibc extension: on Windows `strftime` raises `ValueError: Invalid
format string`. `domain/format.py` exists for this, and `short_date` goes
through it.
"""


def overlapping(first: Segment, start: datetime, end: datetime) -> bool:
    """Whether an existing stretch shares any time with a proposed one.

    An open session is worth the rest of its own day, the reading
    `ledger.end_of_day` gives it everywhere else. Not `wallclock.now()`, which
    would admit a correction for later this afternoon: while a session runs, any
    correction after its start on that date is refused.
    """
    return bool(
        first.start < end and start < first.finish(end_of_day(first.start.date()))
    )
