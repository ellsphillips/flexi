from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from flexi import wallclock
from flexi.constants import EventSource, Portion
from flexi.domain.format import clock, hm, long_date, short_date, spoken, to_the_minute
from flexi.domain.ledger import Segment
from flexi.models.database.db import AbsenceDay, ClockEvent, WorkSession
from flexi.models.database.moment import moment_of
from flexi.services.absence import covers_the_whole_day
from flexi.services.bank_holidays import BankHolidayService
from flexi.services.ledger import segment_of
from flexi.services.settings import SettingsService
from flexi.services.startup import close_stale_sessions
from flexi.services.transactions import write_transaction
from flexi.services.work_sessions import (
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

    def _has_later_work(self, moment: datetime) -> bool:
        """Normal punches advance beyond recorded work; corrections can fill gaps."""
        utc = moment.astimezone(UTC)
        # An offset is strictly less than a day. Older wall readings cannot
        # represent a later instant, even after a timezone change or DST fold.
        cutoff = (
            utc - min(timedelta(days=1), utc - datetime.min.replace(tzinfo=UTC))
        ).replace(tzinfo=None)
        stmt = (
            select(ClockEvent)
            .join(WorkSession, WorkSession.clock_out_id == ClockEvent.id)
            .where(
                WorkSession.voided.is_(False),
                ClockEvent.timestamp >= cutoff,
            )
        )
        return any(moment_of(event) > moment for event in self._session.scalars(stmt))

    def _days_off(self, start: date, end: date) -> list[date]:
        """The dates in a span booked off in full, earliest first.

        `covers_the_whole_day` is the rule the booking side runs on too, so the
        two cannot disagree: a day booked off and then worked is paid for twice.
        One half booked never refuses work, whatever the hour: the ledger halves
        what the day expects, so 11:30 after a morning off counts as 14:00 does.
        """
        stmt = select(AbsenceDay.date, AbsenceDay.portion).where(
            AbsenceDay.date >= start, AbsenceDay.date <= end
        )
        booked: defaultdict[date, list[Portion]] = defaultdict(list)
        for when, portion in self._session.execute(stmt):
            booked[when].append(portion)
        return sorted(
            when for when, portions in booked.items() if covers_the_whole_day(portions)
        )

    def _first_day_off(self, opened: datetime, closed: datetime) -> date | None:
        """The first date booked off in full that a stretch of work reaches.

        Read between the minutes its ends show, as the ledger counts it, and
        endpoints may touch: work ending at 00:00:30 leaves the next day intact.
        """
        opened, closed = to_the_minute(opened), to_the_minute(closed)
        for when in self._days_off(opened.date(), closed.date()):
            midnight = datetime.combine(when, time.min)
            begins = wallclock.local(midnight)
            ends = wallclock.local(midnight + timedelta(days=1))
            if opened < ends and begins < closed:
                return when
        return None

    def sweep(self) -> list[ClockResult]:
        """Close work left running on an earlier day, one result per session.

        The auto-close time can be hours after the person left, so whichever
        surface ran the sweep says what it closed and what it counted.

        The minimum-session preference applies at clock-out, where the person
        sees the decision. Reapplying today's preference to historical rows
        would reinterpret real work on every launch after a config change, so
        startup closes stale sessions and nothing else.
        """
        settings = self._settings.resolved()
        closed = close_stale_sessions(
            self._session, settings.auto_close, contracted=settings.contracted
        )
        return [_left_running(row) for row in closed]

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

            if self._has_later_work(moment):
                return ClockResult(
                    success=False,
                    message=(
                        "Cannot clock in before work already recorded; "
                        "check your system clock or use a correction"
                    ),
                )

            # A known bank holiday blocks clocking in; an unknown calendar
            # answers `None` here and does not.
            if self._holidays.holiday_on(work_date) is not None:
                return ClockResult(
                    success=False, message="Cannot clock in on a bank holiday"
                )

            if self._days_off(work_date, work_date):
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
            day_off = None if short else self._first_day_off(opened, moment)
            if day_off is not None:
                return ClockResult(
                    success=False,
                    message=(
                        f"Work overlaps {short_date(day_off)}, "
                        "which is booked off in full"
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

        A day booked off in full is refused for the same reason: it would be
        paid for twice, once out of the leave balance and once into the flexi
        balance. One half booked leaves the day correctable at any hour, as
        `clock_in` leaves it.

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
            if self._days_off(day, day):
                return ClockResult(
                    success=False,
                    message=CORRECTION_BOOKED.format(day=short_date(day)),
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

    def void(self, session_id: int) -> ClockResult:
        """Take a closed session out of every figure, keeping its events.

        The way back from a forgotten clock-out, a late one or a mistyped
        correction: void it, then record the real hours with `correct`. The
        events stay, being immutable, and the session drops out of the records
        table and every figure derived from it, as one under a minute does.

        A running session is refused: clocking out is what gives it an end.
        """
        with write_transaction(self._session):
            found = self._session.get(WorkSession, session_id)
            if found is None:
                return ClockResult(success=False, message="No such session")
            if found.voided:
                return ClockResult(
                    success=False, message="That session was already voided"
                )
            if found.clock_out_event is None:
                return ClockResult(
                    success=False,
                    message="That session is still running; clock out first",
                    session=found,
                )
            found.voided = True
            message = (
                f"Voided {clock(moment_of(found.clock_in_event))} → "
                f"{clock(moment_of(found.clock_out_event))} on "
                f"{short_date(found.work_date)}"
            )
        return ClockResult(success=True, message=message, session=found)

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

    def segment(self, session_id: int) -> Segment | None:
        """A stretch that still counts, by the id its records row carries.

        Read past the identity map, where a session another copy of Flexi has
        since closed or voided still holds the values it was loaded with.
        """
        stmt = (
            select(WorkSession)
            .execution_options(populate_existing=True)
            .options(
                selectinload(WorkSession.clock_in_event),
                selectinload(WorkSession.clock_out_event),
            )
            .where(WorkSession.id == session_id, WorkSession.voided.is_(False))
        )
        found = self._session.scalars(stmt).one_or_none()
        return None if found is None else segment_of(found)

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


def _left_running(closed: WorkSession) -> ClockResult:
    """What the sweep did to one session: where it closed it, and what counted."""
    segment = segment_of(closed)
    ended = segment.finish(wallclock.now())
    return ClockResult(
        success=True,
        message=(
            f"{short_date(closed.work_date)} was left running and closed at "
            f"{clock(ended)} ({hm(segment.duration(ended))} counted)"
        ),
        session=closed,
    )


def overlapping(first: Segment, start: datetime, end: datetime) -> bool:
    """Whether an existing stretch shares any time with a proposed one.

    An open session claims time after its start until it closes, including
    subsequent dates. Capping it at midnight or now would admit corrections
    that a later clock-out could also claim.
    """
    return first.start < end and (first.end is None or start < first.end)
