from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from flexi import wallclock
from flexi.constants import ClockAction, EventSource
from flexi.domain.format import spoken
from flexi.models.database.db import AbsenceDay, WorkSession
from flexi.models.database.moment import moment_of, punched
from flexi.services.absence import covers_the_whole_day
from flexi.services.bank_holidays import BankHolidayService
from flexi.services.settings import SettingsService
from flexi.services.startup import close_stale_sessions


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
        stmt = select(WorkSession).where(WorkSession.clock_out_id.is_(None))
        return self._session.execute(stmt).scalar_one_or_none()

    def is_clocked_in(self) -> bool:
        return self.get_open_session() is not None

    def sweep(self) -> None:
        """Tidy what an interrupted run left behind, before doing anything else.

        Two halves. A session left running overnight is closed at the
        configured time, and a session so short it can only have been a slip of
        the finger is voided -- which also cleans up databases written before
        there was a threshold.

        Here rather than in `startup`, taking nothing, because both halves are
        already this service's: the session it writes through, and the
        auto-close time its own settings service holds. Passed in, they were
        three arguments of which two were attributes of the third, and the two
        modules imported each other -- one of them from inside a method, to
        make the cycle importable.
        """
        close_stale_sessions(self._session, self._settings.get_auto_close_time())
        self.discard_short_sessions()

    def clock_in(
        self,
        *,
        now: datetime | None = None,
        source: EventSource = EventSource.USER,
    ) -> ClockResult:
        """Clock in. Rejects duplicate clock-in without creating audit rows."""
        self.sweep()
        if self.is_clocked_in():
            return ClockResult(success=False, message="Already clocked in")

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

        event = punched(ClockAction.IN, moment, source=source)
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
        event = punched(ClockAction.OUT, moment, source=source)
        self._session.add(event)
        self._session.flush()

        open_session.clock_out_id = event.id

        # Clocking in and straight back out is a slip of the finger. The events
        # stay — they are immutable, and the audit trail is the point — but the
        # session is voided, so it is absent from the records table and from
        # every figure derived from it.
        length = moment - moment_of(open_session.clock_in_event)

        # A session cannot run backwards. That is a fault in the data, not a
        # slip of the finger, and voiding it deletes real work with no way back:
        # a session opened at 01:30 on the morning the clocks go back used to be
        # discarded here, for up to an hour, with a message blaming the user.
        if length < timedelta():
            self._session.commit()
            return ClockResult(
                success=False,
                message="That clock-out is earlier than the clock-in",
                session=open_session,
            )

        if length < self._minimum:
            open_session.voided = True
            self._session.commit()
            return ClockResult(
                success=True,
                message=f"Discarded — under {spoken(self._minimum)} on the clock",
                session=open_session,
            )

        self._session.commit()
        return ClockResult(
            success=True,
            message="Clocked out",
            session=open_session,
            at=moment,
        )

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
            # Unreachable: `PRAGMA foreign_keys` is on, so a non-null
            # `clock_out_id` always resolves. The check is here because the
            # relationship is typed optional and `moment_of` below is not.
            # `tests/services/test_short_sessions.py` pins the constraint that
            # makes this dead, so dropping the pragma fails there.
            if work.clock_out_event is None:  # pragma: no cover
                continue
            length = moment_of(work.clock_out_event) - moment_of(work.clock_in_event)
            if timedelta() <= length < self._minimum:
                work.voided = True
                discarded.append(work)
        if discarded:
            self._session.commit()
        return discarded
