"""Building day ledgers, in one pass over a period.

This is the service the interface actually reads. The v1 code issued a query per
day per concern — a bank-holiday lookup, an absence lookup and a session lookup
for each of 31 rows, which is roughly 150 round trips to redraw one table, on a
widget that redraws on a timer.

Here a period is loaded with three queries regardless of its length, and the
results are memoised until something writes. ``invalidate()`` is called by the
dashboard when a :class:`~flexi.services.registry.DataChanged` scope says the
underlying rows moved; nothing else clears the cache, so a redraw provoked by a
resize costs nothing.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from flexi import wallclock
from flexi.constants import DayKind
from flexi.domain.balance import (
    BalanceSummary,
    accumulate,
    expected_for,
    toil_taken_for,
    worked_from,
)
from flexi.domain.ledger import AbsenceSlice, DayLedger, Segment
from flexi.domain.punch import Window
from flexi.models.database.db import (
    AbsenceDay,
    BalanceAdjustment,
    BankHolidayCache,
    WorkSession,
)
from flexi.services.settings import SettingsService


def _naive(moment: datetime) -> datetime:
    """A timestamp as local wall time, without a zone.

    Everything on screen is wall-clock: a punch strip is drawn against the hours
    of the day the wearer lived, not against UTC. Timestamps are stored aware and
    compared naive, in one place, so the conversion cannot be forgotten in a
    widget.
    """
    if moment.tzinfo is None:
        return moment
    return moment.astimezone().replace(tzinfo=None)


class LedgerService:
    """Turn stored rows into :class:`~flexi.domain.ledger.DayLedger` values."""

    def __init__(
        self,
        session: Session,
        settings: SettingsService,
        division: str = "england-and-wales",
    ) -> None:
        self._session = session
        self._settings = settings
        self._division = division
        self._cache: dict[date, DayLedger] = {}

    # -- cache -------------------------------------------------------------

    def invalidate(self) -> None:
        """Forget every ledger built so far."""
        self._cache.clear()

    # -- reading -----------------------------------------------------------

    @property
    def window(self) -> Window:
        """The span of the day the punch strip should draw."""
        start, end = self._settings.get_day_window()
        return Window.parse(start, end)

    @property
    def contracted(self) -> timedelta:
        """How long a standard working day is."""
        return self._settings.get_contracted()

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
        moment = now or wallclock.now()
        today = moment.date()

        wanted = _date_range(start, end)
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
        year = self._settings.active_leave_year(as_of)
        month, day = self._settings.get_leave_year_start()
        return self.summary(date(year, month, day), as_of, now=now)

    # -- building ----------------------------------------------------------

    def _build(self, start: date, end: date, moment: datetime, today: date) -> None:
        sessions = self._sessions(start, end)
        absences = self._absences(start, end)
        holidays = self._holidays(start, end)
        corrections = self._adjustments(start, end)
        working_days = set(self._settings.get_working_day_indices())
        contracted = self.contracted

        for when in _date_range(start, end):
            segments = tuple(
                sorted(
                    (_segment(row) for row in sessions[when]),
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
                segments, now=moment if when >= today else _end_of(when)
            )
            expected = expected_for(
                contracted,
                is_working_day=is_working,
                is_holiday=title is not None,
                absences=slices,
            )

            self._cache[when] = DayLedger(
                date=when,
                kind=_kind(title, slices, segments, is_working=is_working),
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
        totals: dict[date, timedelta] = {}
        for row in self._session.execute(stmt):
            totals[row.date] = totals.get(row.date, timedelta()) + timedelta(
                minutes=row.minutes
            )
        return totals

    def _holidays(self, start: date, end: date) -> dict[date, str]:
        stmt = select(BankHolidayCache.date, BankHolidayCache.title).where(
            BankHolidayCache.division == self._division,
            BankHolidayCache.date >= start,
            BankHolidayCache.date <= end,
        )
        return {row.date: row.title for row in self._session.execute(stmt)}


def _end_of(day: date) -> datetime:
    """The last moment of a date.

    A session nobody closed is worth the rest of its own day, not every hour
    since. Startup auto-closes stale sessions, so this only catches the window
    between a crash and the next launch -- but during it, an open Tuesday would
    otherwise report Tuesday to now as time worked on Tuesday.
    """
    return datetime.combine(day, time.max)


def _segment(row: WorkSession) -> Segment:
    start = _naive(row.clock_in_event.timestamp)
    end = (
        _naive(row.clock_out_event.timestamp)
        if row.clock_out_event is not None
        else None
    )
    return Segment(
        session_id=row.id,
        start=start,
        end=end,
        auto_closed=row.auto_closed,
        note=row.note,
    )


def _kind(
    holiday: str | None,
    slices: tuple[AbsenceSlice, ...],
    segments: tuple[Segment, ...],
    *,
    is_working: bool,
) -> DayKind:
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


def _date_range(start: date, end: date) -> list[date]:
    span = (end - start).days
    return [start + timedelta(days=offset) for offset in range(max(0, span) + 1)]


def utc_now() -> datetime:
    """The current moment, aware, for anything being written to the database."""
    return datetime.now(tz=UTC)
