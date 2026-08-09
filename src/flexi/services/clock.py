from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from flexi import wallclock
from flexi.constants import ClockAction
from flexi.models.database.db import AbsenceDay, ClockEvent, WorkSession


def _naive(moment: datetime) -> datetime:
    """A moment as local wall time, without a zone.

    Flexi stores the time a person lived, not an instant on a global timeline,
    because that is what a timesheet is. An aware value can still arrive from a
    caller or from a row written by an older version, and it is converted rather
    than stripped: 08:44+00:00 is 09:44 to somebody on BST, and stripping the
    zone would record it as 08:44 and lose them an hour.
    """
    if moment.tzinfo is None:
        return moment
    return moment.astimezone().replace(tzinfo=None)


SECONDS_PER_MINUTE = 60


def _readable(span: timedelta) -> str:
    """A threshold as somebody would say it out loud."""
    seconds = int(span.total_seconds())
    if seconds % SECONDS_PER_MINUTE == 0 and seconds >= SECONDS_PER_MINUTE:
        minutes = seconds // SECONDS_PER_MINUTE
        return f"{minutes} minute" + ("" if minutes == 1 else "s")
    return f"{seconds} second" + ("" if seconds == 1 else "s")


@dataclass(frozen=True)
class ClockResult:
    """Result of a clock action."""

    success: bool
    message: str
    event: ClockEvent | None = None
    session: WorkSession | None = None


DEFAULT_MINIMUM_SESSION = timedelta(seconds=60)
"""Below this, a session is a slip of the finger rather than a minute of work."""


class ClockService:
    """Atomic clock-in / clock-out operations."""

    def __init__(
        self,
        session: Session,
        minimum_session: timedelta = DEFAULT_MINIMUM_SESSION,
    ) -> None:
        self._session = session
        self._minimum = minimum_session

    def get_open_session(self) -> WorkSession | None:
        """Return the currently open work session, or None."""
        stmt = select(WorkSession).where(WorkSession.clock_out_id.is_(None))
        return self._session.execute(stmt).scalar_one_or_none()

    def is_clocked_in(self) -> bool:
        return self.get_open_session() is not None

    def _run_stale_cleanup(self) -> None:
        """Run stale-session cleanup before clock actions."""
        from flexi.services.startup import run_startup_cleanup

        run_startup_cleanup(self._session)

    def clock_in(
        self,
        *,
        now: datetime | None = None,
        source: str = "user",
    ) -> ClockResult:
        """Clock in. Rejects duplicate clock-in without creating audit rows."""
        self._run_stale_cleanup()
        if self.is_clocked_in():
            return ClockResult(success=False, message="Already clocked in")

        if now is None:
            now = wallclock.now()

        work_date = _naive(now).date()

        # Block clocking on bank holidays (if data available)
        from flexi.services.bank_holidays import BankHolidayService

        bh_svc = BankHolidayService(self._session)
        bh = bh_svc.is_bank_holiday(work_date)
        if bh is True:
            return ClockResult(
                success=False, message="Cannot clock in on a bank holiday"
            )

        stmt = select(AbsenceDay).where(AbsenceDay.date == work_date)
        if self._session.execute(stmt).scalar_one_or_none() is not None:
            return ClockResult(
                success=False, message="Cannot clock in on an absence day"
            )

        event = ClockEvent(action=ClockAction.IN, timestamp=_naive(now), source=source)
        self._session.add(event)
        self._session.flush()

        work_session = WorkSession(
            clock_in_id=event.id,
            work_date=work_date,
        )
        self._session.add(work_session)
        self._session.commit()

        return ClockResult(
            success=True,
            message="Clocked in",
            event=event,
            session=work_session,
        )

    def clock_out(
        self,
        *,
        now: datetime | None = None,
        source: str = "user",
    ) -> ClockResult:
        """Clock out. Rejects clock-out without an open session."""
        open_session = self.get_open_session()
        if open_session is None:
            return ClockResult(success=False, message="Not clocked in")

        if now is None:
            now = wallclock.now()

        event = ClockEvent(action=ClockAction.OUT, timestamp=_naive(now), source=source)
        self._session.add(event)
        self._session.flush()

        open_session.clock_out_id = event.id

        # Clocking in and straight back out is a slip of the finger. The events
        # stay — they are immutable, and the audit trail is the point — but the
        # session is voided, so it is absent from the records table and from
        # every figure derived from it.
        length = _naive(now) - _naive(open_session.clock_in_event.timestamp)
        if length < self._minimum:
            open_session.voided = True
            self._session.commit()
            return ClockResult(
                success=True,
                message=f"Discarded — under {_readable(self._minimum)} on the clock",
                event=event,
                session=open_session,
            )

        self._session.commit()
        return ClockResult(
            success=True,
            message="Clocked out",
            event=event,
            session=open_session,
        )

    def get_sessions_for_date(self, work_date: date) -> list[WorkSession]:
        """Every session that counts on a date. Voided ones are not sessions."""
        stmt = select(WorkSession).where(
            WorkSession.work_date == work_date, WorkSession.voided.is_(False)
        )
        return list(self._session.execute(stmt).scalars())

    def discard_short_sessions(self) -> list[WorkSession]:
        """Void every closed session already on record that is too short.

        For databases that predate the threshold, or that were filled in while
        somebody was learning which key does what.
        """
        stmt = select(WorkSession).where(
            WorkSession.clock_out_id.is_not(None), WorkSession.voided.is_(False)
        )
        discarded: list[WorkSession] = []
        for work in self._session.execute(stmt).scalars():
            if work.clock_out_event is None:
                continue
            length = _naive(work.clock_out_event.timestamp) - _naive(
                work.clock_in_event.timestamp
            )
            if length < self._minimum:
                work.voided = True
                discarded.append(work)
        if discarded:
            self._session.commit()
        return discarded
