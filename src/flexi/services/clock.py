from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from flexi import wallclock
from flexi.constants import EventSource
from flexi.domain.format import hm, short_date, spoken
from flexi.domain.ledger import Segment
from flexi.models.database.db import AbsenceDay, WorkSession
from flexi.models.database.moment import moment_of
from flexi.services.absence import covers_the_whole_day
from flexi.services.bank_holidays import BankHolidayService
from flexi.services.ledger import segment_of
from flexi.services.settings import SettingsService
from flexi.services.startup import close_stale_sessions
from flexi.services.transactions import atomic, write_transaction
from flexi.services.work_sessions import (
    stage_clock_in,
    stage_clock_out,
    stage_correction,
)

__all__ = (
    "CORRECTION_BACKWARDS",
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

    The status bar stamps a clock message with it -- "Clocked out at 12:04" is
    a fact somebody can check against the wall, which is what makes a mistaken
    keystroke visible the moment it happens. It carried the whole `ClockEvent`
    before, so the screen reached past the `Outcome` protocol with two
    `getattr`s and then decided *whether* to stamp by asking whether the
    message began with the word "Clocked"."""


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

        This service used to build its own `BankHolidayService` inside
        `clock_in`, which took the default division and ignored the configured
        one, and to accept a default minimum that disagreed with the value the
        registry hands every other surface. Both were invisible because both
        had somewhere plausible to fall back to.
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

    def sweep(self) -> None:
        """Close work left running on an earlier day.

        A minimum-session preference applies when a person clocks out and sees
        the decision immediately. Reapplying today's preference to historical
        rows here would silently reinterpret real work on every launch after a
        config change, so startup deliberately performs only stale closure.
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
            # The refusal carries the session, so a caller does not have to go back
            # and ask what is already in hand.
            running = self.get_open_session()
            if running is not None:
                return ClockResult(
                    success=False, message="Already clocked in", session=running
                )

            moment = wallclock.local(now) if now is not None else wallclock.now()
            work_date = moment.date()

            # Block clocking on bank holidays (if data available)
            if self._holidays.holiday_on(work_date) is not None:
                return ClockResult(
                    success=False, message="Cannot clock in on a bank holiday"
                )

            # `scalar_one_or_none` here raised outright on two rows, which is the
            # one arrangement `AbsenceService` documents as legal: a sick morning
            # and an annual afternoon. Booking those made the next morning's
            # `flexi clock in` a traceback.
            stmt = select(AbsenceDay.portion).where(AbsenceDay.date == work_date)
            booked = self._session.execute(stmt).scalars().all()
            if covers_the_whole_day(booked):
                return ClockResult(
                    success=False, message="Cannot clock in on an absence day"
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
        open_session = self.get_open_session()
        if open_session is None:
            return ClockResult(success=False, message="Not clocked in")

        moment = wallclock.local(now) if now is not None else wallclock.now()
        length = moment - moment_of(open_session.clock_in_event)

        # A session cannot run backwards. That is a fault in the data, not a
        # slip of the finger, and voiding it deletes real work with no way back:
        # a session opened at 01:30 on the morning the clocks go back used to be
        # discarded here, for up to an hour, with a message blaming the user.
        if length < timedelta():
            return ClockResult(
                success=False,
                message="That clock-out is earlier than the clock-in",
                session=open_session,
            )

        short = length < self._minimum
        with atomic(self._session):
            closed = stage_clock_out(
                self._session,
                open_session.id,
                moment,
                source=source,
                voided=short,
            )
        if not closed:
            return ClockResult(success=False, message="Not clocked in")

        # Clocking in and straight back out is a slip of the finger. The events
        # stay — they are immutable, and the audit trail is the point — but the
        # session is voided, so it is absent from the records table and from
        # every figure derived from it.
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

    # -- corrections -------------------------------------------------------

    def correct(
        self,
        day: date,
        opened: time,
        closed: time,
        *,
        now: date | None = None,
    ) -> ClockResult:
        """Record work on a day nobody clocked at the time.

        A morning nobody punched in for is still a morning that was worked, and
        the alternative to recording it is a balance that is quietly wrong.

        Refused rather than reconciled when it overlaps something already there:
        two stretches sharing an hour is a day that counts it twice, and no rule
        for merging them is better than a person looking at both and saying
        which is right.
        """
        today = now or wallclock.today()
        if day > today:
            return ClockResult(success=False, message=CORRECTION_FUTURE)
        if closed < opened:
            return ClockResult(success=False, message=CORRECTION_BACKWARDS)
        if closed == opened:
            return ClockResult(success=False, message=CORRECTION_EMPTY)

        opened_at = wallclock.local(datetime.combine(day, opened))
        closed_at = wallclock.local(datetime.combine(day, closed))
        with write_transaction(self._session):
            if any(
                overlapping(existing, opened_at, closed_at)
                for existing in self.segments_on(day)
            ):
                return ClockResult(
                    success=False, message=CORRECTION_OVERLAP.format(day=day)
                )
            recorded = stage_correction(self._session, opened_at, closed_at, day)

        return ClockResult(
            success=True,
            message=f"Recorded {hm(closed_at - opened_at)} on {short_date(day)}",
            session=recorded,
            at=opened_at,
        )

    def corrections_between(self, start: date, end: date) -> list[Segment]:
        """Every corrected stretch in a span, earliest first.

        Only the corrections: a review of what was typed in rather than clocked
        is a list somebody reads to check their own work, and a punched session
        on the same day is not what they came to look at.
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
        stmt = (
            select(WorkSession)
            .where(WorkSession.work_date == day, WorkSession.voided.is_(False))
            .options(
                selectinload(WorkSession.clock_in_event),
                selectinload(WorkSession.clock_out_event),
            )
        )
        return [segment_of(row) for row in self._session.scalars(stmt)]


CORRECTION_BACKWARDS = "That correction ends before it starts"
CORRECTION_EMPTY = "A correction has to cover some time"
CORRECTION_FUTURE = "A day that has not happened cannot be corrected"
CORRECTION_OVERLAP = "That overlaps work already recorded on {day:%a %-d %b}"


def overlapping(first: Segment, start: datetime, end: datetime) -> bool:
    """Whether an existing stretch shares any time with a proposed one."""
    return bool(first.start < end and start < (first.end or first.start))
