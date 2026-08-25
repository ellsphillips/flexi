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

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time

from sqlalchemy import select
from sqlalchemy.orm import Session

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


def covers_the_whole_day(booked: Iterable[Portion]) -> bool:
    """True when what is booked leaves no half of the day left to work.

    One rule, asked from both sides. `_has_work_in` already lets a half day be
    booked over work in the other half; without this, the clock refused the
    mirror image — you could book a sick morning after working it, and then not
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
        after = self.toil_after
        if after is None or after >= 0:
            return None
        return f"This takes {deficit(abs(after))}"


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

    def get_absence(self, day: date) -> AbsenceDay | None:
        """The first absence on a date, or ``None``.

        Kept for callers that only need to know whether the day is spoken for.
        Anything drawing the day should use :meth:`for_date`, which can see both
        halves.
        """
        found = self.for_date(day)
        return found[0] if found else None

    def has_absence(self, day: date) -> bool:
        """True when anything at all is booked on the date."""
        return bool(self.for_date(day))

    def booked_days(self, day: date) -> float:
        """How much of the date is booked, as a fraction of a working day."""
        return sum(absence.portion.days for absence in self.for_date(day))

    def is_fully_absent(self, day: date) -> bool:
        """True when no part of the date is available to work."""
        return self.booked_days(day) >= 1.0

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
        start: date | None = None,
        end: date | None = None,
        *,
        valid_only: bool = False,
    ) -> float:
        """How many *days* of a type were booked, counting a half as a half."""
        rows = self._rows_of_type(absence_type, start, end, valid_only=valid_only)
        return sum(row.portion.days for row in rows)

    def count_absences(
        self,
        absence_type: AbsenceType,
        start: date | None = None,
        end: date | None = None,
        *,
        valid_only: bool = False,
    ) -> int:
        """How many absence *rows* of a type exist in the range.

        Distinct from :meth:`count_days`: two half-days are two occurrences and
        one day. Sickness is worth reporting both ways, because "five occasions"
        and "two and a half days" say very different things about a year.
        """
        return len(self._rows_of_type(absence_type, start, end, valid_only=valid_only))

    def _rows_of_type(
        self,
        absence_type: AbsenceType,
        start: date | None,
        end: date | None,
        *,
        valid_only: bool,
    ) -> list[AbsenceDay]:
        stmt = select(AbsenceDay).where(AbsenceDay.absence_type == absence_type)
        if start is not None:
            stmt = stmt.where(AbsenceDay.date >= start)
        if end is not None:
            stmt = stmt.where(AbsenceDay.date <= end)
        rows = list(self._session.execute(stmt).scalars())
        if valid_only:
            rows = [row for row in rows if self._is_valid_marker(row)]
        return rows

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
        booked = self.count_days(
            AbsenceType.ANNUAL, start=start, end=end, valid_only=True
        )
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
        refusal = self._refusal(day, absence_type, portion, note)
        if refusal is not None:
            return refusal

        absence = AbsenceDay(
            date=day,
            absence_type=absence_type,
            portion=portion,
            note=note,
        )
        self._session.add(absence)
        self._session.commit()

        return AbsenceResult(
            success=True,
            message=f"{absence_type.label} booked for {short_date(day)}",
            absence=absence,
            warning=self._toil_warning(absence_type, portion, available_toil_days),
        )

    def _refusal(
        self,
        day: date,
        absence_type: AbsenceType,
        portion: Portion,
        note: str | None,
    ) -> AbsenceResult | None:
        """The first reason this booking cannot happen, or ``None``."""
        verdict, reason, _ = self._verdict(
            day, absence_type, portion, note, remaining_annual=None
        )
        if verdict is Verdict.BOOK:
            return None
        return AbsenceResult(False, reason)

    def _verdict(
        self,
        day: date,
        absence_type: AbsenceType,
        portion: Portion,
        note: str | None,
        *,
        remaining_annual: float | None,
    ) -> tuple[Verdict, str, str | None]:
        """What booking this date would do, typed, with the sentence to show.

        Order matters: cheapest first, and only the first is reported, because
        a dialog listing four objections at once tells nobody what to do next.

        ``remaining_annual`` is passed in rather than read, so a plan can carry a
        drawdown the database has not seen. ``None`` means read it fresh.
        """
        if absence_type.requires_note and not (note or "").strip():
            return (
                Verdict.NEEDS_NOTE,
                "Other absence needs a note saying what it is",
                None,
            )

        if not self._settings.is_working_day(day.weekday()):
            return (Verdict.NON_WORKING, "Not a working day", None)

        holiday = self._bank_holidays.is_bank_holiday(day)
        if holiday is None:
            return (
                Verdict.NO_CALENDAR,
                "Bank holiday data unavailable; cannot book absence",
                None,
            )
        if holiday:
            title = self._bank_holidays.get_title(day)
            return (Verdict.BANK_HOLIDAY, "That day is already a bank holiday", title)

        clash = self._clash_refusal(day, portion)
        if clash is not None:
            return (Verdict.CLASH, clash.message, None)

        if absence_type.draws_down_entitlement:
            remaining = (
                self.get_remaining_annual_leave(day)
                if remaining_annual is None
                else remaining_annual
            )
            if remaining is not None and remaining < portion.days:
                short = portion.days - remaining
                return (
                    Verdict.NO_ENTITLEMENT,
                    f"Not enough annual leave — {short:g} day short of the request",
                    None,
                )

        return (Verdict.BOOK, "", None)

    def _clash_refusal(self, day: date, portion: Portion) -> AbsenceResult | None:
        """Whether something already occupies the part of the day being booked."""
        existing = self.for_date(day)
        if any(booked.portion is Portion.FULL for booked in existing):
            return AbsenceResult(False, "That day is already booked in full")
        if existing and portion is Portion.FULL:
            return AbsenceResult(
                False, "Half of that day is already booked; remove it first"
            )
        if any(booked.portion is portion for booked in existing):
            return AbsenceResult(
                False, f"That {portion.label.lower()} is already booked"
            )
        if self._has_work_in(day, portion):
            return AbsenceResult(
                False, "There is recorded work in that part of the day"
            )
        return None

    def _toil_warning(
        self,
        absence_type: AbsenceType,
        portion: Portion,
        available_toil_days: float | None,
    ) -> str | None:
        """A note about overdrawing the flexi balance, which is allowed."""
        if not absence_type.draws_down_balance or available_toil_days is None:
            return None
        if available_toil_days >= portion.days:
            return None
        return f"Booked, but this takes {deficit(portion.days - available_toil_days)}"

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
        remaining = self.get_remaining_annual_leave(start)
        days: list[PlannedDay] = []

        for when in days_between(start, end):
            verdict, reason, detail = self._verdict(
                when, absence_type, portion, note, remaining_annual=remaining
            )
            days.append(PlannedDay(when, verdict, reason, detail))
            drawing_down = verdict is Verdict.BOOK and (
                absence_type.draws_down_entitlement
            )
            if drawing_down and remaining is not None:
                remaining -= portion.days

        return AbsencePlan(
            absence_type=absence_type,
            portion=portion,
            note=note,
            start=start,
            end=end,
            days=tuple(days),
            annual_remaining=self.get_remaining_annual_leave(start),
            toil_available=available_toil_days,
        )

    def book_plan(self, plan: AbsencePlan) -> RangeResult:
        """Write exactly what the plan decided, and nothing it did not."""
        booked: list[date] = []
        skipped: list[tuple[date, str]] = []

        for day in plan.days:
            if day.verdict is Verdict.BOOK:
                self._session.add(
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

        if booked:
            self._session.commit()
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
        tally: dict[tuple[AbsenceType, Portion], int] = {}
        for row in self.in_range(start, end):
            tally[row.absence_type, row.portion] = (
                tally.get((row.absence_type, row.portion), 0) + 1
            )
        lots = tuple((kind, portion, count) for (kind, portion), count in tally.items())
        return RemovalPlan(start, end, lots)

    def clear_range(self, start: date, end: date) -> RangeResult:
        """Remove every booking in a span.

        A day with nothing on it is not a failure — clearing a fortnight that
        happens to be half empty should report what it removed, not five
        complaints about the days that were already free.
        """
        cleared = [
            when
            for when in days_between(start, end)
            if self.for_date(when) and self.remove(when).success
        ]
        return RangeResult(tuple(cleared))

    def change_type(
        self,
        day: date,
        new_type: AbsenceType,
        portion: Portion = Portion.FULL,
        *,
        note: str | None = None,
    ) -> AbsenceResult:
        """Change what an existing booking is recorded as."""
        absence = next(
            (booked for booked in self.for_date(day) if booked.portion is portion), None
        )
        if absence is None:
            return AbsenceResult(False, "Nothing is booked for that part of the day")

        if new_type.requires_note and not (note or absence.note or "").strip():
            return AbsenceResult(False, "Other absence needs a note saying what it is")

        if new_type.draws_down_entitlement and absence.absence_type is not new_type:
            remaining = self.get_remaining_annual_leave(day)
            if remaining is not None and remaining < portion.days:
                return AbsenceResult(False, "Not enough annual leave for that change")

        absence.absence_type = new_type
        if note is not None:
            absence.note = note
        self._session.commit()
        return AbsenceResult(True, f"Changed to {new_type.label}", absence)

    def remove(self, day: date, portion: Portion | None = None) -> AbsenceResult:
        """Remove one portion's booking, or everything booked on the date."""
        booked = self.for_date(day)
        if portion is not None:
            booked = [absence for absence in booked if absence.portion is portion]
        if not booked:
            return AbsenceResult(False, "Nothing is booked on that date")

        removed = booked[0].absence_type.label if len(booked) == 1 else "Absence"
        for absence in booked:
            self._session.delete(absence)
        self._session.commit()
        return AbsenceResult(True, f"{removed} removed from {short_date(day)}")

    # -- internals ---------------------------------------------------------

    def _has_work_in(self, day: date, portion: Portion) -> bool:
        """True when recorded work overlaps the half of the day being booked."""
        sessions = self._sessions_on(day)
        if not sessions:
            return False
        if portion is Portion.FULL:
            return True
        # Both boundaries are localised, because the ones they are compared
        # against come from `moment_of` and are aware. A manufactured wall time
        # sitting next to a stored one has to be given the same offset or the
        # comparison is a TypeError rather than an answer.
        midday = wallclock.local(datetime.combine(day, time(MIDDAY_HOUR, 0)))
        for work in sessions:
            start = moment_of(work.clock_in_event)
            end = (
                moment_of(work.clock_out_event)
                if work.clock_out_event is not None
                else wallclock.local(datetime.combine(day, time.max))
            )
            if portion is Portion.AM and start < midday:
                return True
            if portion is Portion.PM and end > midday:
                return True
        return False

    def _sessions_on(self, day: date) -> Sequence[WorkSession]:
        stmt = select(WorkSession).where(
            WorkSession.work_date == day, WorkSession.voided.is_(False)
        )
        return list(self._session.execute(stmt).scalars())

    def _is_valid_marker(self, absence: AbsenceDay) -> bool:
        """True when the marker still sits on a day it could legally be booked on.

        A working pattern that later drops Fridays leaves last year's Friday
        bookings in place. They stop counting against the allowance rather than
        being deleted behind the user's back.
        """
        if not self._settings.is_working_day(absence.date.weekday()):
            return False
        return self._bank_holidays.is_bank_holiday(absence.date) is not True
