"""Building day ledgers, in one pass over a period.

A period loads in three queries regardless of its length, and the results are
memoised until something writes. The dashboard calls ``invalidate()`` when a
:class:`~flexi.messages.Scope` says the rows moved; nothing else clears the
cache, so a redraw provoked by a resize costs nothing.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta

from sqlalchemy import event, select
from sqlalchemy.orm import Session, selectinload

from flexi import wallclock
from flexi.constants import DayKind
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

__all__ = ("LedgerService", "day_kind", "end_of_day", "segment_of")


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
        event.listen(session, "after_commit", self.invalidate_after_commit)

    # -- cache -------------------------------------------------------------

    def invalidate(self) -> None:
        """Forget every ledger built so far."""
        self._cache.clear()

    def invalidate_after_commit(self, committed: Session) -> None:
        """Forget derived values after their source session commits.

        Cache correctness belongs beside the cache. Requiring every screen,
        command, and future service caller to remember ``invalidate`` after a
        successful write made a stale balance the default failure mode. The
        registry constructs one ledger per session, so this observer follows
        the same lifetime and covers every write path through that session.
        """
        if committed is self._session:
            self.invalidate()

    # -- reading -----------------------------------------------------------

    @property
    def window(self) -> Window:
        """The span of the day the punch strip should draw."""
        return Window.parse(*self._settings.get_day_window())

    def day(self, when: date, *, now: datetime | None = None) -> DayLedger:
        """One day's ledger."""
        return self.days(when, when, now=now)[0]

    def days(
        self, start: date, end: date, *, now: datetime | None = None
    ) -> list[DayLedger]:
        """Every day between two dates, inclusive, built in three queries.

        A day already in the cache is reused unless it is *today* — today's
        ledger contains any open session, whose length changes every second, so
        caching it would freeze the live readout.
        """
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

        Accumulated rather than stored, so a corrected session or a changed
        contract is reflected everywhere at once and there is no derived total to
        fall out of step.
        """
        as_of = as_of or wallclock.today()
        month, day = self._settings.get_leave_year_start()
        return self.summary(leaveyear.start_of(as_of, month, day), as_of, now=now)

    # -- building ----------------------------------------------------------

    def _build(self, start: date, end: date, moment: datetime, today: date) -> None:
        sessions = self._sessions(start, end)
        absences = self._absences(start, end)
        # `or {}`: "none in this span" and "no calendar at all" are the
        # service's distinction to make and to report. The ledger cannot
        # invent holidays it has not been told about either way, and what it
        # must not do is read the cache table itself.
        holidays = self._holidays_service.titles_between(start, end) or {}
        corrections = self._adjustments(start, end)
        settings = self._settings.resolved()
        working_days = set(settings.working_days)
        contracted = settings.contracted

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

            worked = worked_from(
                segments, now=moment if when >= today else end_of_day(when)
            )
            expected = expected_for(
                contracted,
                is_working_day=is_working,
                is_holiday=title is not None,
                absences=slices,
            )

            self._cache[when] = DayLedger(
                date=when,
                kind=day_kind(title, slices, segments, is_working=is_working),
                is_working_day=is_working,
                contracted=contracted,
                worked=worked,
                expected=expected,
                toil_taken=toil_taken_for(contracted, slices),
                adjustment=corrections.get(when, timedelta()),
                holiday_title=title,
                absences=slices,
                segments=segments,
            )

    def _sessions(self, start: date, end: date) -> defaultdict[date, list[WorkSession]]:
        # Eager-load both events. They are what a segment is made of, so a lazy
        # relationship turns "three queries for a period" into three plus two per
        # session — 34 round trips for a month, which is the shape this service
        # exists to avoid.
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

        Carried on the day rather than added at the end, so a period summary and
        the running balance pick them up by the same route as every other term.
        A correction dated before the leave year belongs to the leave year it
        was dated in, and is not counted here — which is what makes it possible
        to settle one year without disturbing the next.
        """
        stmt = select(BalanceAdjustment.date, BalanceAdjustment.minutes).where(
            BalanceAdjustment.date >= start, BalanceAdjustment.date <= end
        )
        # Declared as a plain mapping, though it is built as a `defaultdict`.
        # The consumer reads it with `.get(when, timedelta())` on purpose:
        # advertising a defaultdict would invite a caller to index it and grow
        # the mapping silently while iterating a span.
        totals: defaultdict[date, timedelta] = defaultdict(timedelta)
        for row in self._session.execute(stmt):
            totals[row.date] += timedelta(minutes=row.minutes)
        return totals


def end_of_day(day: date) -> datetime:
    """The last moment of a date.

    A session nobody closed is worth the rest of its own day, not every hour
    since. Startup auto-closes stale sessions, so this only catches the window
    between a crash and the next launch -- but during it, an open Tuesday would
    otherwise report Tuesday to now as time worked on Tuesday.
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
        note=row.note,
    )


def day_kind(
    holiday: str | None,
    slices: tuple[AbsenceSlice, ...],
    segments: tuple[Segment, ...],
    *,
    is_working: bool,
) -> DayKind:
    """What a date is, from what is recorded against it.

    The one place the five kinds are decided. `PARTIAL` is the case a
    one-status-per-day table gets wrong: a half-day absence with work in the
    other half.
    """
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
