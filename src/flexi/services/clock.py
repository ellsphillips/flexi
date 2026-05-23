from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from flexi.constants import ClockAction
from flexi.models.database.db import ClockEvent, WorkSession


@dataclass(frozen=True)
class ClockResult:
    """Result of a clock action."""

    success: bool
    message: str
    event: ClockEvent | None = None
    session: WorkSession | None = None


class ClockService:
    """Atomic clock-in / clock-out operations."""

    def __init__(self, session: Session) -> None:
        self._session = session

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
            now = datetime.now(tz=timezone.utc)

        work_date = now.astimezone().date()

        # Block clocking on bank holidays (if data available)
        from flexi.services.bank_holidays import BankHolidayService

        bh_svc = BankHolidayService(self._session)
        bh = bh_svc.is_bank_holiday(work_date)
        if bh is True:
            return ClockResult(
                success=False, message="Cannot clock in on a bank holiday"
            )

        # Block clocking on absence-marked dates (table may not exist yet)
        try:
            from flexi.models.database.db import AbsenceDay

            stmt = select(AbsenceDay).where(AbsenceDay.date == work_date)
            if self._session.execute(stmt).scalar_one_or_none() is not None:
                return ClockResult(
                    success=False, message="Cannot clock in on an absence day"
                )
        except Exception:  # noqa: BLE001
            pass  # absence table may not exist yet

        event = ClockEvent(action=ClockAction.IN, timestamp=now, source=source)
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
            now = datetime.now(tz=timezone.utc)

        event = ClockEvent(action=ClockAction.OUT, timestamp=now, source=source)
        self._session.add(event)
        self._session.flush()

        open_session.clock_out_id = event.id
        self._session.commit()

        return ClockResult(
            success=True,
            message="Clocked out",
            event=event,
            session=open_session,
        )

    def get_sessions_for_date(self, work_date: date) -> list[WorkSession]:
        """Return all work sessions for a given date."""
        stmt = select(WorkSession).where(WorkSession.work_date == work_date)
        return list(self._session.execute(stmt).scalars())
