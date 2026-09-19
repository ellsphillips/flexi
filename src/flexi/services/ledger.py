"""Build day ledgers in one pass over a period.

A period loads in three queries whatever its length, and the results are
memoised. The cache clears when the session commits or rolls back, when another
connection commits, and when a :class:`~flexi.messages.Scope` says the rows
moved, so a redraw provoked by a resize costs nothing.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import event, select
from sqlalchemy.orm import Session, selectinload

from flexi import wallclock
from flexi.constants import DayKind, EventSource
from flexi.domain import leaveyear
from flexi.domain.balance import (
    BalanceSummary,
    accumulate,
    expected_for,
    toil_taken_for,
    worked_from,
)
from flexi.domain.dates import days_between
from flexi.domain.ledger import AbsenceSlice, DayLedger, Segment
from flexi.domain.punch import Window
from flexi.models.database.db import (
    AbsenceDay,
    BalanceAdjustment,
    WorkSession,
)
from flexi.models.database.moment import moment_of
from flexi.services.bank_holidays import BankHolidayService
from flexi.services.settings import SettingsService

__all__ = (
    "LedgerRevision",
    "LedgerService",
    "day_kind",
    "end_of_day",
    "ledger_revision",
    "segment_of",
)


@dataclass(frozen=True, slots=True)
class LedgerRevision:
    """The SQLite connection and external-commit counter behind a derivation."""

    connection: object
    data_version: int


def ledger_revision(session: Session) -> LedgerRevision:
    """Identify the source state against which ledger rows are cached.

    ``PRAGMA data_version`` changes when another SQLite connection commits. Its
    number means nothing on any other connection, so the connection identity
    travels with it and a different pooled connection counts as a new revision.
    """
    connection = session.connection()
    driver = connection.connection.dbapi_connection
    if driver is None:
        msg = "A ledger revision requires an active database connection"
        raise RuntimeError(msg)
    version = connection.exec_driver_sql("PRAGMA data_version").scalar_one()
    if not isinstance(version, int):
        msg = "SQLite returned an invalid data_version"
        raise TypeError(msg)
    return LedgerRevision(driver, version)


class LedgerService:
    """Turn stored rows into :class:`~flexi.domain.ledger.DayLedger` values."""

    def __init__(
        self,
        session: Session,
        settings: SettingsService,
        holidays: BankHolidayService,
    ) -> None:
        self._session = session
        self._settings = settings
        self._holidays_service = holidays
        self._cache: dict[date, DayLedger] = {}
        self._revision: LedgerRevision | None = None
        event.listen(session, "after_commit", self.invalidate_after_transaction)
        event.listen(session, "after_rollback", self.invalidate_after_transaction)

    # Cache.

    def invalidate(self) -> None:
        """Forget every ledger built so far."""
        self._cache.clear()

    def invalidate_after_transaction(self, completed: Session) -> None:
        """Forget derived values after the source session commits or rolls back.

        A rollback may release the connection whose ``data_version`` is being
        compared, and it leaves derivations built over rows that never committed.
        """
        if completed is self._session:
            self.invalidate()

    def refresh_revision(self) -> None:
        """Invalidate when another connection committed since the last read."""
        current = ledger_revision(self._session)
        if self._revision is not None and current != self._revision:
            self.invalidate()
        self._revision = current

    # Reading.

    @property
    def window(self) -> Window:
        """The span of the day the punch strip should draw."""
        return self._settings.get_day_window()

    def day(self, when: date, *, now: datetime | None = None) -> DayLedger:
        """One day's ledger."""
        return self.days(when, when, now=now)[0]

    def days(
        self, start: date, end: date, *, now: datetime | None = None
    ) -> list[DayLedger]:
        """Every day between two dates, inclusive, built in three queries.

        A cached day is reused unless it is *today*: today's ledger holds any
        open session, whose length changes every second.
        """
        self.refresh_revision()
        moment = wallclock.local(now) if now is not None else wallclock.now()
        today = moment.date()

        wanted = days_between(start, end)
        missing = [day for day in wanted if day not in self._cache or day == today]
        if missing:
            self._build(min(missing), max(missing), moment, today)
        return [self._cache[day] for day in wanted]

    def summary(
        self, start: date, end: date, *, now: datetime | None = None
    ) -> BalanceSummary:
        """Worked, expected and withdrawn over a span."""
        return accumulate(self.days(start, end, now=now))

    def balance(
        self, as_of: date | None = None, *, now: datetime | None = None
    ) -> BalanceSummary:
        """The running flexi balance, from the start of the leave year to a date.

        Accumulated on every read, never stored, so a corrected session or a
        changed contract takes effect everywhere at once.
        """
        as_of = as_of or wallclock.today()
        month, day = self._settings.get_leave_year_start()
        return self.summary(leaveyear.start_of(as_of, month, day), as_of, now=now)

    # Building.

    def _build(self, start: date, end: date, moment: datetime, today: date) -> None:
        sessions = self._sessions(start, end)
        absences = self._absences(start, end)
        # `titles_between` returns None for "no calendar"; the ledger treats
        # that as "no holidays" and leaves the distinction to the service.
        holidays = self._holidays_service.titles_between(start, end) or {}
        corrections = self._adjustments(start, end)
        settings = self._settings.resolved()
        working_days = set(settings.working_days)
        contracted = settings.contracted
        tracking_since = settings.tracking_since

        for when in days_between(start, end):
            segments = tuple(
                sorted(
                    (segment_of(row) for row in sessions[when]),
                    key=lambda item: item.start,
                )
            )
            slices = tuple(
                AbsenceSlice(row.id, row.absence_type, row.portion, row.note)
                for row in absences[when]
            )
            title = holidays.get(when)
            is_working = when.weekday() in working_days
            # A real punch tracks the day whatever `tracking_since` says; an
            # amended one cannot, since nothing was clocking at the time.
            punched = any(not segment.amended for segment in segments)
            is_tracked = tracking_since is None or when >= tracking_since or punched

            worked = worked_from(
                segments, now=moment if when >= today else end_of_day(when)
            )
            expected = expected_for(
                contracted,
                is_tracked=is_tracked,
                is_working_day=is_working,
                is_holiday=title is not None,
                absences=slices,
            )

            self._cache[when] = DayLedger(
                date=when,
                kind=day_kind(
                    title,
                    slices,
                    segments,
                    is_working=is_working,
                    # Broader than the test `expected` uses: a corrected
                    # pre-setup day asks for nothing but is still known about.
                    is_tracked=is_tracked or bool(segments),
                ),
                is_working_day=is_working,
                contracted=contracted,
                worked=worked,
                expected=expected,
                # TOIL is a withdrawal against what a day asks for, and an
                # untracked day or a bank holiday asks for nothing.
                toil_taken=(
                    toil_taken_for(contracted, slices)
                    if is_tracked and title is None
                    else timedelta()
                ),
                adjustment=corrections.get(when, timedelta()),
                holiday_title=title,
                absences=slices,
                segments=segments,
            )

    def _sessions(self, start: date, end: date) -> defaultdict[date, list[WorkSession]]:
        # Eager-load both events: a segment is made of them, and a lazy
        # relationship would cost two more queries per session.
        stmt = (
            select(WorkSession)
            .options(
                selectinload(WorkSession.clock_in_event),
                selectinload(WorkSession.clock_out_event),
            )
            .where(
                WorkSession.work_date >= start,
                WorkSession.work_date <= end,
                WorkSession.voided.is_(False),
            )
            .order_by(WorkSession.work_date, WorkSession.id)
        )
        grouped: defaultdict[date, list[WorkSession]] = defaultdict(list)
        for row in self._session.execute(stmt).scalars():
            grouped[row.work_date].append(row)
        return grouped

    def _absences(self, start: date, end: date) -> defaultdict[date, list[AbsenceDay]]:
        stmt = (
            select(AbsenceDay)
            .where(AbsenceDay.date >= start, AbsenceDay.date <= end)
            .order_by(AbsenceDay.date, AbsenceDay.id)
        )
        grouped: defaultdict[date, list[AbsenceDay]] = defaultdict(list)
        for row in self._session.execute(stmt).scalars():
            grouped[row.date].append(row)
        return grouped

    def _adjustments(self, start: date, end: date) -> dict[date, timedelta]:
        """Stored corrections, by the date they take effect.

        Carried on the day, so a period summary and the running balance pick
        them up by the same route as every other term. A correction dated
        before the span belongs to its own leave year and is not counted here.
        """
        stmt = select(BalanceAdjustment.date, BalanceAdjustment.minutes).where(
            BalanceAdjustment.date >= start, BalanceAdjustment.date <= end
        )
        # Returned as a plain mapping: indexing a defaultdict would grow it
        # while a span is iterated, so callers use `.get(when, timedelta())`.
        totals: defaultdict[date, timedelta] = defaultdict(timedelta)
        for row in self._session.execute(stmt):
            totals[row.date] += timedelta(minutes=row.minutes)
        return totals


def end_of_day(day: date) -> datetime:
    """The last moment of a date.

    An unclosed session is worth the rest of its own day, not every hour since,
    so a past day's open session stops at midnight.
    """
    return wallclock.local(datetime.combine(day, time.max))


def segment_of(row: WorkSession) -> Segment:
    start = moment_of(row.clock_in_event)
    end = moment_of(row.clock_out_event) if row.clock_out_event is not None else None
    return Segment(
        session_id=row.id,
        start=start,
        end=end,
        auto_closed=row.auto_closed,
        # Both events of a correction carry AMENDED; the clock-in always exists.
        amended=row.clock_in_event.source is EventSource.AMENDED,
        note=row.note,
    )


def day_kind(
    holiday: str | None,
    slices: tuple[AbsenceSlice, ...],
    segments: tuple[Segment, ...],
    *,
    is_working: bool,
    is_tracked: bool,
) -> DayKind:
    """What a date is, from what is recorded against it.

    The six kinds are decided here, in precedence order. `UNTRACKED` comes
    first, so a day before setup is never labelled a weekend or a holiday;
    `_build` settles before this call that a day with work on it is tracked.
    `PARTIAL` is a half-day absence with work in the other half.
    """
    if not is_tracked:
        return DayKind.UNTRACKED
    if holiday is not None:
        return DayKind.HOLIDAY
    if not is_working:
        return DayKind.WEEKEND
    if slices and segments:
        return DayKind.PARTIAL
    booked = sum(item.portion.days for item in slices)
    if booked >= 1.0:
        return DayKind.ABSENT
    if slices:
        return DayKind.PARTIAL
    return DayKind.WORKING
