"""Booking, changing and removing absence.

Every refusal is a sentence the status bar can show unedited, which is why there
is no modal explaining why a key did nothing.

The rules SQLite cannot express, so this service does:

* A full day cannot coexist with a half. The table constraint is only
  ``(date, portion)``.
* Two halves of different types are legal -- a sick morning and an annual
  afternoon is a real thing that happens.
* A half day may be booked over recorded work in the other half.
* TOIL warns rather than blocks: an annual allowance is somebody else's limit,
  a flexi balance is your own arithmetic.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from flexi import wallclock
from flexi.constants import AbsenceType, Portion, Verdict
from flexi.domain import leaveyear
from flexi.domain.dates import days_between
from flexi.domain.format import days as fmt_days
from flexi.domain.format import plural, short_date
from flexi.domain.ledger import MIDDAY_HOUR
from flexi.models.database.db import AbsenceDay, WorkSession
from flexi.models.database.moment import moment_of
from flexi.services.bank_holidays import BankHolidayService
from flexi.services.settings import SettingsService
from flexi.services.transactions import atomic

__all__ = (
    "AbsencePlan",
    "AbsenceResult",
    "AbsenceService",
    "DayFacts",
    "PlannedDay",
    "RangeResult",
    "RemovalPlan",
    "Span",
    "Tally",
    "clash_reason",
    "covers_the_whole_day",
    "deficit",
    "overdraw",
    "span_of",
    "still_bookable",
    "verdict_for",
)

type Span = tuple[datetime, datetime]
"""A stretch of recorded work, resolved: an open session ends at midnight."""


def covers_the_whole_day(booked: Iterable[Portion]) -> bool:
    """True when what is booked leaves no half of the day left to work.

    One rule, asked from both sides. `DayFacts.has_work_in` already lets a half
    day be booked over work in the other half; without this, the clock refused
    the mirror image — you could book a sick morning after working it, and then not
    work the afternoon after booking the morning.

    Examples:
        >>> covers_the_whole_day([Portion.FULL])
        True
        >>> covers_the_whole_day([Portion.AM, Portion.PM])
        True
        >>> covers_the_whole_day([Portion.AM])
        False
        >>> covers_the_whole_day([])
        False
    """
    portions = set(booked)
    return Portion.FULL in portions or {Portion.AM, Portion.PM} <= portions


def deficit(shortfall: float) -> str:
    """How far past zero this goes, phrased once for both places that say it.

    Booking a day of TOIL and previewing a week of it are the same news, and
    assembling the sentence twice is how one of them came to read "3 day".

    Examples:
        >>> deficit(1)
        'the flexi balance 1 day into deficit'
        >>> deficit(2.5)
        'the flexi balance 2.5 days into deficit'
    """
    short = f"{fmt_days(shortfall)} {plural(shortfall, 'day')}"
    return f"the flexi balance {short} into deficit"


def overdraw(after: float | None, *, opening: str = "This") -> str | None:
    """The sentence for a balance this would leave under zero, or ``None``.

    ``after`` is what the flexi balance would read once the booking is made,
    and ``None`` means the booking does not touch it -- annual leave does not,
    so a balance already in deficit is not this booking's news, and saying so
    on every annual booking teaches people to ignore the line.

    One rule, asked by the single booking and by the plan. They had their own
    arithmetic and their own sentence, so a day and a fortnight could disagree
    about whether the same request was worth mentioning.

    Examples:
        >>> overdraw(None) is None
        True
        >>> overdraw(1.5) is None
        True
        >>> overdraw(-2.5)
        'This takes the flexi balance 2.5 days into deficit'
        >>> overdraw(-1, opening="Booked, but this")
        'Booked, but this takes the flexi balance 1 day into deficit'
    """
    if after is None or after >= 0:
        return None
    return f"{opening} takes {deficit(-after)}"


@dataclass(frozen=True)
class AbsenceResult:
    """The outcome of an absence action, and what to tell the user about it."""

    success: bool
    message: str
    absence: AbsenceDay | None = None
    warning: str | None = None


@dataclass(frozen=True)
class RangeResult:
    """The outcome of booking or clearing a span of days.

    Partial by design. Booking a fortnight that crosses a bank holiday should
    book twelve days and say so, not refuse all fourteen and leave somebody to
    find the offending one — so this records what happened and what did not,
    with the reason each day was skipped.
    """

    booked: tuple[date, ...] = ()
    skipped: tuple[tuple[date, str], ...] = ()
    warning: str | None = None

    @property
    def success(self) -> bool:
        return bool(self.booked)

    @property
    def reasons(self) -> tuple[str, ...]:
        """The distinct refusals, in the order they were first hit."""
        return tuple(dict.fromkeys(reason for _when, reason in self.skipped))

    def message(self, what: str) -> str:
        """One sentence a status bar can show unedited."""
        if not self.booked and not self.skipped:
            return "Nothing to do"
        if not self.booked:
            return (
                self.reasons[0]
                if len(self.reasons) == 1
                else (f"Nothing {what}: " + "; ".join(self.reasons))
            )
        days = f"{len(self.booked)} {plural(len(self.booked), 'day')}"
        if not self.skipped:
            return f"{days} {what}"
        missed = f"{len(self.skipped)} skipped"
        return f"{days} {what}, {missed} — {'; '.join(self.reasons)}"


@dataclass(frozen=True, slots=True)
class PlannedDay:
    """One date, and what booking it would do."""

    date: date
    verdict: Verdict
    reason: str
    detail: str | None = None


class Tally(NamedTuple):
    """How much of a kind of absence a span holds, counted both ways.

    Two half-days are two occurrences and one day. Sickness is worth reporting
    both ways: "five occasions" and "two and a half days" say very different
    things about a year.
    """

    days: float
    occurrences: int


@dataclass(frozen=True, slots=True)
class DayFacts:
    """Everything deciding one date needs, and nothing that needs a database.

    The rules used to read for themselves, one date at a time: a settings row,
    two bank-holiday rows, an absence row and a session row per day. Planning a
    year of leave was 1,417 round trips to answer a question about 365 dates,
    where `LedgerService` -- which loads a period in three queries however long
    it is -- had already shown what that should cost.

    Plain values rather than the rows they were read from, so
    :func:`verdict_for` is a function of its arguments and can be exercised
    without a session.
    """

    date: date
    is_working_day: bool
    has_calendar: bool
    """False only when there is no bank holiday calendar at all, which is not
    the same as a date with no holiday on it."""
    holiday_title: str | None
    booked: tuple[Portion, ...]
    worked: tuple[Span, ...]

    @property
    def midday(self) -> datetime:
        """The boundary between the two halves of this date.

        Localised, because the spans in ``worked`` are: a manufactured wall time
        compared against a stored one has to be given the same offset or the
        comparison is a `TypeError` rather than an answer.
        """
        return wallclock.local(datetime.combine(self.date, time(MIDDAY_HOUR, 0)))

    def has_work_in(self, portion: Portion) -> bool:
        """True when recorded work overlaps the half of the day being booked."""
        if not self.worked:
            return False
        if portion is Portion.FULL:
            return True
        midday = self.midday
        return any(
            start < midday if portion is Portion.AM else end > midday
            for start, end in self.worked
        )


def clash_reason(facts: DayFacts, portion: Portion) -> str | None:
    """Why this part of the day is already spoken for, or ``None``.

    Ordered cheapest first, and only the first is reported: a dialog listing
    four objections at once tells nobody what to do next.
    """
    if Portion.FULL in facts.booked:
        return "That day is already booked in full"
    if facts.booked and portion is Portion.FULL:
        return "Half of that day is already booked; remove it first"
    if portion in facts.booked:
        return f"That {portion.label.lower()} is already booked"
    if facts.has_work_in(portion):
        return "There is recorded work in that part of the day"
    return None


def verdict_for(
    facts: DayFacts,
    absence_type: AbsenceType,
    portion: Portion,
    note: str | None = None,
    *,
    remaining_annual: float | None = None,
) -> PlannedDay:
    """What booking this date would do, typed, with the sentence to show.

    Order matters: cheapest first, and only the first objection is reported.

    ``remaining_annual`` is passed in rather than read, so a plan can carry a
    drawdown the database has not seen -- booking ten days against five left has
    to refuse the sixth, and the rows for the first five are not written yet.
    ``None`` means no entitlement has been recorded, which is not the same as
    none remaining and must not refuse anything.
    """
    if absence_type.requires_note and not (note or "").strip():
        return PlannedDay(
            facts.date,
            Verdict.NEEDS_NOTE,
            "Other absence needs a note saying what it is",
        )
    if not facts.is_working_day:
        return PlannedDay(facts.date, Verdict.NON_WORKING, "Not a working day")
    if not facts.has_calendar:
        return PlannedDay(
            facts.date,
            Verdict.NO_CALENDAR,
            "Bank holiday data unavailable; cannot book absence",
        )
    if facts.holiday_title is not None:
        return PlannedDay(
            facts.date,
            Verdict.BANK_HOLIDAY,
            "That day is already a bank holiday",
            facts.holiday_title,
        )
    clash = clash_reason(facts, portion)
    if clash is not None:
        return PlannedDay(facts.date, Verdict.CLASH, clash)
    if (
        absence_type.draws_down_entitlement
        and remaining_annual is not None
        and remaining_annual < portion.days
    ):
        short = portion.days - remaining_annual
        return PlannedDay(
            facts.date,
            Verdict.NO_ENTITLEMENT,
            f"Not enough annual leave — {short:g} day short of the request",
        )
    return PlannedDay(facts.date, Verdict.BOOK, "")


def still_bookable(
    when: date, working_days: Iterable[int], holidays: frozenset[date] | None
) -> bool:
    """True when a marker still sits on a day it could legally be booked on.

    A working pattern that later drops Fridays leaves last year's Friday
    bookings in place. They stop counting against the allowance rather than
    being deleted behind the user's back.

    ``holidays`` is ``None`` when there is no calendar, in which case nothing
    can be ruled out for being one.

    Examples:
        >>> friday, saturday = date(2026, 6, 12), date(2026, 6, 13)
        >>> still_bookable(friday, [0, 1, 2, 3, 4], frozenset())
        True
        >>> still_bookable(saturday, [0, 1, 2, 3, 4], frozenset())
        False
        >>> still_bookable(friday, range(7), frozenset({friday}))
        False
        >>> still_bookable(friday, range(7), None)
        True
    """
    return when.weekday() in set(working_days) and (
        holidays is None or when not in holidays
    )


@dataclass(frozen=True, slots=True)
class AbsencePlan:
    """What booking a span would do, decided without writing anything.

    Exists so a confirmation prompt can be a question rather than a receipt.
    ``book_range`` used to call ``book`` in a loop, and ``book`` commits, so by
    the time there was a result to show the rows were already in the database.
    """

    absence_type: AbsenceType
    portion: Portion
    note: str | None
    start: date
    end: date
    days: tuple[PlannedDay, ...]
    annual_remaining: float | None = None
    toil_available: float | None = None

    @property
    def bookable(self) -> tuple[PlannedDay, ...]:
        return tuple(d for d in self.days if d.verdict is Verdict.BOOK)

    @property
    def refused(self) -> tuple[PlannedDay, ...]:
        return tuple(d for d in self.days if d.verdict.is_refusal)

    @property
    def skipped(self) -> tuple[PlannedDay, ...]:
        """Weekends and bank holidays: passed over, not turned down."""
        return tuple(d for d in self.days if d.verdict.is_skip)

    @property
    def cost(self) -> float:
        """Days this plan would consume, counting a half as a half."""
        return len(self.bookable) * self.portion.days

    @property
    def is_empty(self) -> bool:
        return not self.bookable

    @property
    def reasons(self) -> tuple[str, ...]:
        """The distinct refusals, in the order they were first hit."""
        return tuple(dict.fromkeys(day.reason for day in self.refused))

    @property
    def headline(self) -> str:
        """What this plan would do, in one line.

        Says "of fourteen" only when the two differ, so a clean span reads as a
        statement and a partial one reads as a question.
        """
        booked = len(self.bookable)
        span = f" of {len(self.days)}" if booked != len(self.days) else ""
        return f"{booked} {plural(booked, 'day')}{span}, {fmt_days(self.cost)} used"

    @property
    def annual_after(self) -> float | None:
        if self.annual_remaining is None:
            return None
        if not self.absence_type.draws_down_entitlement:
            return self.annual_remaining
        return self.annual_remaining - self.cost

    @property
    def toil_after(self) -> float | None:
        if self.toil_available is None:
            return None
        if not self.absence_type.draws_down_balance:
            return self.toil_available
        return self.toil_available - self.cost

    @property
    def warning(self) -> str | None:
        """Overdrawing the flexi balance is allowed, and worth saying out loud.

        Only when *this* plan does the overdrawing. Annual leave does not touch
        the balance, so a balance that was already in deficit is not news, and
        saying so on every annual booking teaches people to ignore the line.
        """
        if not self.absence_type.draws_down_balance:
            return None
        return overdraw(self.toil_after)


@dataclass(frozen=True, slots=True)
class RemovalPlan:
    """What clearing a span would take back, decided without deleting anything.

    The counterpart to `AbsencePlan`. Booking a fortnight says which days it
    will take and what they cost; removing one used to say "Remove 9 bookings?"
    — a number with no way to tell nine days of annual leave from nine sick
    mornings, which is the whole of what somebody is being asked to approve.
    """

    start: date
    end: date
    lots: tuple[tuple[AbsenceType, Portion, int], ...]
    """Each kind and portion present, with how many of it, in booking order."""

    @property
    def count(self) -> int:
        """How many bookings would go."""
        return sum(count for _kind, _portion, count in self.lots)

    @property
    def is_empty(self) -> bool:
        return not self.lots

    @property
    def summary(self) -> str:
        """One line per kind, so nine of one is never nine of another."""
        return "\n".join(
            f"  {count} {plural(count, portion.noun)} of {kind.phrase}"
            for kind, portion, count in self.lots
        )


class AbsenceService:
    """Manage absence markers."""

    def __init__(
        self,
        session: Session,
        settings: SettingsService,
        bank_holidays: BankHolidayService,
    ) -> None:
        self._session = session
        self._settings = settings
        self._bank_holidays = bank_holidays

    # -- reading -----------------------------------------------------------

    def for_date(self, day: date) -> list[AbsenceDay]:
        """Every absence booked on a date: none, one full day, or up to two halves."""
        stmt = select(AbsenceDay).where(AbsenceDay.date == day).order_by(AbsenceDay.id)
        return list(self._session.execute(stmt).scalars())

    def by_id(self, absence_id: int) -> AbsenceDay | None:
        """One booking, by the key a table row carries.

        The records table hands a row key back as a primary key, and the screen
        turned it into a row by fetching every booking in the visible period and
        scanning for it -- a year of them, when the period was zoomed to a year.
        """
        return self._session.get(AbsenceDay, absence_id)

    def in_range(self, start: date, end: date) -> list[AbsenceDay]:
        """Every absence between two dates, inclusive, in date order."""
        stmt = (
            select(AbsenceDay)
            .where(AbsenceDay.date >= start, AbsenceDay.date <= end)
            .order_by(AbsenceDay.date, AbsenceDay.id)
        )
        return list(self._session.execute(stmt).scalars())

    # -- counting ----------------------------------------------------------

    def count_days(
        self,
        absence_type: AbsenceType,
        start: date,
        end: date,
        *,
        valid_only: bool = False,
    ) -> float:
        """How many *days* of a type were booked in a span, a half counting half.

        ``valid_only`` drops markers on days the working pattern no longer
        covers: an allowance is drawn against days that could be booked, while
        the balance is drawn against days that were.
        """
        stmt = select(AbsenceDay).where(
            AbsenceDay.absence_type == absence_type,
            AbsenceDay.date >= start,
            AbsenceDay.date <= end,
        )
        rows = list(self._session.execute(stmt).scalars())
        if valid_only:
            rows = self._only_still_bookable(rows)
        return sum(row.portion.days for row in rows)

    def tally(self, start: date, end: date) -> dict[AbsenceType, Tally]:
        """Days and occurrences of every type in a span, in one pass.

        Counting only markers that still sit on a day leave could be booked on,
        because the wallet is the only thing that asks and an allowance is what
        it is drawing. Every type appears, including the ones with nothing
        against them: a gauge that vanishes when it reaches zero is a gauge
        somebody has to remember existed.

        The wallet used to ask for days and occurrences separately, per type:
        ten scans of the same rows with byte-identical arguments, each
        re-validating every row it read with three queries of its own. A year
        of twenty-five bookings cost 162 round trips to produce ten pairs of
        numbers.
        """
        rows = self._only_still_bookable(self.in_range(start, end))
        # `Counter` counts rows; days are halves, and a Counter is typed to
        # integers.
        days: defaultdict[AbsenceType, float] = defaultdict(float)
        for row in rows:
            days[row.absence_type] += row.portion.days
        occurrences = Counter(row.absence_type for row in rows)
        return {kind: Tally(days[kind], occurrences[kind]) for kind in AbsenceType}

    def _only_still_bookable(self, rows: list[AbsenceDay]) -> list[AbsenceDay]:
        """Drop markers whose date is no longer one leave could be booked on.

        The two questions are asked once for the whole list rather than once per
        row. Asked per row they were three queries each, so validating a leave
        year cost more than reading it did.
        """
        if not rows:
            return rows
        working = self._settings.get_working_day_indices()
        holidays = self._bank_holidays.get_dates()
        known = None if holidays is None else frozenset(holidays)
        return [row for row in rows if still_bookable(row.date, working, known)]

    def leave_year_bounds(self, ref: date | None = None) -> tuple[date, date]:
        """The first and last date of the leave year containing ``ref``."""
        month, day = self._settings.get_leave_year_start()
        return leaveyear.bounds(ref or wallclock.today(), month, day)

    def get_remaining_annual_leave(self, ref: date | None = None) -> float | None:
        """Days of annual leave left in the active leave year, or ``None``.

        ``None`` means no entitlement has been recorded — which is not the same
        as none remaining, and the interface must not draw it as zero.
        """
        entitlement = self._settings.get_active_entitlement_days(ref)
        if entitlement is None:
            return None
        start, end = self.leave_year_bounds(ref)
        booked = self.count_days(AbsenceType.ANNUAL, start, end, valid_only=True)
        return entitlement - booked

    # -- writing -----------------------------------------------------------

    def book(
        self,
        day: date,
        absence_type: AbsenceType,
        portion: Portion = Portion.FULL,
        *,
        note: str | None = None,
        available_toil_days: float | None = None,
    ) -> AbsenceResult:
        """Book an absence, or say why not.

        ``available_toil_days`` only warns on a TOIL booking that would overdraw; it
        never refuses one.
        """
        decided = verdict_for(
            self.facts_between(day, day)[0],
            absence_type,
            portion,
            note,
            remaining_annual=(
                self.get_remaining_annual_leave(day)
                if absence_type.draws_down_entitlement
                else None
            ),
        )
        if decided.verdict is not Verdict.BOOK:
            return AbsenceResult(False, decided.reason)

        after = (
            available_toil_days - portion.days
            if absence_type.draws_down_balance and available_toil_days is not None
            else None
        )
        absence = AbsenceDay(
            date=day,
            absence_type=absence_type,
            portion=portion,
            note=note,
        )
        with atomic(self._session):
            self._session.add(absence)

        return AbsenceResult(
            success=True,
            message=f"{absence_type.label} booked for {short_date(day)}",
            absence=absence,
            warning=overdraw(after, opening="Booked, but this"),
        )

    # -- what deciding a date needs -----------------------------------------

    def facts_between(self, start: date, end: date) -> list[DayFacts]:
        """Everything :func:`verdict_for` reads, for a whole span, in one pass.

        Four reads however long the span is, against six per day: a settings
        row, two bank-holiday rows, an absence row and a session row each. It is
        the shape `LedgerService` uses, and for the same reason.

        Not asked of `LedgerService` itself, close as the two are. It memoises
        until something writes, and a verdict read through a stale cache would
        book over a day that had just been taken.
        """
        working = set(self._settings.resolved().working_days)
        titles = self._bank_holidays.titles_between(start, end)
        booked: defaultdict[date, list[Portion]] = defaultdict(list)
        for row in self.in_range(start, end):
            booked[row.date].append(row.portion)
        worked: defaultdict[date, list[Span]] = defaultdict(list)
        for session in self._sessions_between(start, end):
            worked[session.work_date].append(span_of(session))

        return [
            DayFacts(
                date=when,
                is_working_day=when.weekday() in working,
                has_calendar=titles is not None,
                holiday_title=None if titles is None else titles.get(when),
                booked=tuple(booked[when]),
                worked=tuple(worked[when]),
            )
            for when in days_between(start, end)
        ]

    def _sessions_between(self, start: date, end: date) -> list[WorkSession]:
        """Live sessions in a span, with both their clock events loaded.

        Eagerly, because a span is resolved into moments the instant it is read
        and a lazy relationship would put two queries back on every one of them.
        """
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
        return list(self._session.execute(stmt).scalars())

    # -- planning -----------------------------------------------------------

    def plan(
        self,
        start: date,
        end: date,
        absence_type: AbsenceType,
        portion: Portion = Portion.FULL,
        *,
        note: str | None = None,
        available_toil_days: float | None = None,
    ) -> AbsencePlan:
        """Decide every date in the span without writing a row.

        The entitlement is drawn down across the plan rather than read fresh for
        each day: booking ten days against five left has to refuse the sixth,
        and the database has not seen the first five yet.
        """
        entitlement = self.get_remaining_annual_leave(start)
        remaining = entitlement
        days: list[PlannedDay] = []

        for facts in self.facts_between(start, end):
            decided = verdict_for(
                facts, absence_type, portion, note, remaining_annual=remaining
            )
            days.append(decided)
            if (
                decided.verdict is Verdict.BOOK
                and absence_type.draws_down_entitlement
                and remaining is not None
            ):
                remaining -= portion.days

        return AbsencePlan(
            absence_type=absence_type,
            portion=portion,
            note=note,
            start=start,
            end=end,
            days=tuple(days),
            annual_remaining=entitlement,
            toil_available=available_toil_days,
        )

    def book_plan(self, plan: AbsencePlan) -> RangeResult:
        """Write exactly what the plan decided, and nothing it did not."""
        booked: list[date] = []
        rows: list[AbsenceDay] = []
        skipped: list[tuple[date, str]] = []

        for day in plan.days:
            if day.verdict is Verdict.BOOK:
                rows.append(
                    AbsenceDay(
                        date=day.date,
                        absence_type=plan.absence_type,
                        portion=plan.portion,
                        note=plan.note,
                    )
                )
                booked.append(day.date)
            elif day.verdict.is_refusal:
                skipped.append((day.date, day.reason))

        if rows:
            with atomic(self._session):
                self._session.add_all(rows)
        return RangeResult(tuple(booked), tuple(skipped), plan.warning)

    def book_range(
        self,
        start: date,
        end: date,
        absence_type: AbsenceType,
        portion: Portion = Portion.FULL,
        *,
        note: str | None = None,
        available_toil_days: float | None = None,
    ) -> RangeResult:
        """Book every day in a span that will take it.

        Weekends and bank holidays are passed over quietly — nobody booking a
        fortnight means to book the Saturdays, and reporting them as refusals
        would bury the one that matters. They are still in the plan, so a caller
        that wants to say "and I left the two bank holidays" can.
        """
        return self.book_plan(
            self.plan(
                start,
                end,
                absence_type,
                portion,
                note=note,
                available_toil_days=available_toil_days,
            )
        )

    def removal_plan(self, start: date, end: date) -> RemovalPlan:
        """What clearing a span would take back, without taking any of it back."""
        counted = Counter(
            (row.absence_type, row.portion) for row in self.in_range(start, end)
        )
        lots = tuple(
            (kind, portion, count) for (kind, portion), count in counted.items()
        )
        return RemovalPlan(start, end, lots)

    def clear_range(self, start: date, end: date) -> RangeResult:
        """Remove every booking in a span.

        A day with nothing on it is not a failure — clearing a fortnight that
        happens to be half empty should report what it removed, not five
        complaints about the days that were already free.

        One read and one commit for the whole span. Walking the dates and
        calling `remove` per date read every date twice -- `remove` opens by
        re-reading what the guard had just read -- and committed once per
        booking: 415 queries and 25 commits to clear a year.
        """
        rows = self.in_range(start, end)
        if not rows:
            return RangeResult()
        with atomic(self._session):
            for row in rows:
                self._session.delete(row)
        # Distinct dates, in order: `in_range` orders by date, and a date
        # holding both halves is one date cleared, not two.
        return RangeResult(tuple(dict.fromkeys(row.date for row in rows)))

    def remove(self, day: date, portion: Portion | None = None) -> AbsenceResult:
        """Remove one portion's booking, or everything booked on the date."""
        booked = self.for_date(day)
        if portion is not None:
            booked = [absence for absence in booked if absence.portion is portion]
        if not booked:
            return AbsenceResult(False, "Nothing is booked on that date")

        removed = booked[0].absence_type.label if len(booked) == 1 else "Absence"
        with atomic(self._session):
            for absence in booked:
                self._session.delete(absence)
        return AbsenceResult(True, f"{removed} removed from {short_date(day)}")


def span_of(session: WorkSession) -> Span:
    """When a session ran, resolved.

    A session nobody closed is worth the rest of its own day: `LedgerService`
    reaches the same conclusion by the same route, and a clock-out that never
    came must not make the whole evening look worked.
    """
    start = moment_of(session.clock_in_event)
    if session.clock_out_event is None:
        return start, wallclock.local(datetime.combine(session.work_date, time.max))
    return start, moment_of(session.clock_out_event)
