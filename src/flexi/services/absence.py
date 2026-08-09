"""Booking, changing and removing absence.

Every refusal here is a sentence the status bar can show without editing. That is
not politeness — it is the reason the interface needs no modal to explain why a
key did nothing.

The rules that are not obvious:

* A **full day cannot coexist with a half**, and SQLite cannot say so, so this
  service says it. The database constraint is only ``(date, portion)``.
* **Two halves of different types are legal.** A sick morning and an annual
  afternoon is a real thing that happens, and refusing it would push the user
  into recording a lie.
* **A half day may be booked over existing work in the other half.** Someone who
  worked the morning and went home ill at lunch has to be able to record both.
* **TOIL warns, it does not block.** An annual allowance is a hard limit set by
  someone else; a flexi balance is your own arithmetic, and going into deficit is
  a decision rather than an error.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from flexi.constants import AbsenceType, Portion
from flexi.domain.ledger import MIDDAY_HOUR
from flexi.models.database.db import AbsenceDay, WorkSession
from flexi.services.bank_holidays import BankHolidayService
from flexi.services.settings import SettingsService


def _walk(start: date, end: date) -> list[date]:
    """Every date from start to end, inclusive."""
    span = (end - start).days
    return [start + timedelta(days=offset) for offset in range(max(0, span) + 1)]


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
        seen: list[str] = []
        for _when, reason in self.skipped:
            if reason not in seen:
                seen.append(reason)
        return tuple(seen)

    def message(self, what: str) -> str:
        """One sentence a status bar can show unedited."""
        if not self.booked and not self.skipped:
            return "Nothing to do"
        if not self.booked:
            return self.reasons[0] if len(self.reasons) == 1 else (
                f"Nothing {what}: " + "; ".join(self.reasons)
            )
        days = f"{len(self.booked)} day" + ("" if len(self.booked) == 1 else "s")
        if not self.skipped:
            return f"{days} {what}"
        missed = f"{len(self.skipped)} skipped"
        return f"{days} {what}, {missed} — {'; '.join(self.reasons)}"


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

    def get_absences_in_range(self, start: date, end: date) -> list[AbsenceDay]:
        """Alias of :meth:`in_range`."""
        return self.in_range(start, end)

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
        ref = ref or date.today()
        year = self._settings.active_leave_year(ref)
        month, day = self._settings.get_leave_year_start()
        start = date(year, month, day)
        try:
            following = date(year + 1, month, day)
        except ValueError:  # 29 February
            following = date(year + 1, month, day - 1)
        return start, following - timedelta(days=1)

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

        Args:
            day: The date to book.
            absence_type: What kind of absence.
            portion: A whole day, a morning or an afternoon.
            note: Required for :attr:`~flexi.constants.AbsenceType.OTHER`.
            available_toil_days: The flexi balance in days, when the caller knows
                it. Used only to *warn* on a TOIL booking that would overdraw.
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
            message=f"{absence_type.label} booked for {day.strftime('%a %-d %b')}",
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
        if absence_type.requires_note and not (note or "").strip():
            return AbsenceResult(False, "Other absence needs a note saying what it is")

        if not self._settings.is_working_day(day.weekday()):
            return AbsenceResult(False, "That is not a working day")

        holiday = self._bank_holidays.is_bank_holiday(day)
        if holiday is None:
            return AbsenceResult(
                False, "Bank holiday data unavailable; cannot book absence"
            )
        if holiday:
            return AbsenceResult(False, "That day is already a bank holiday")

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

        if absence_type.draws_down_entitlement:
            remaining = self.get_remaining_annual_leave(day)
            if remaining is not None and remaining < portion.days:
                short = portion.days - remaining
                return AbsenceResult(
                    False,
                    f"Not enough annual leave — {short:g} day short of the request",
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
        overdraft = portion.days - available_toil_days
        return f"Booked, but this takes the flexi balance {overdraft:g} day into deficit"

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

        Weekends and bank holidays are skipped quietly — nobody booking a
        fortnight means to book the Saturdays, and reporting them as refusals
        would bury the one that matters.
        """
        booked: list[date] = []
        skipped: list[tuple[date, str]] = []
        warning: str | None = None
        remaining = available_toil_days

        for when in _walk(start, end):
            if not self._settings.is_working_day(when.weekday()):
                continue
            result = self.book(
                when,
                absence_type,
                portion,
                note=note,
                available_toil_days=remaining,
            )
            if result.success:
                booked.append(when)
                warning = warning or result.warning
                if remaining is not None:
                    remaining -= portion.days
            elif "bank holiday" in result.message:
                continue
            else:
                skipped.append((when, result.message))

        return RangeResult(tuple(booked), tuple(skipped), warning)

    def clear_range(self, start: date, end: date) -> RangeResult:
        """Remove every booking in a span.

        A day with nothing on it is not a failure — clearing a fortnight that
        happens to be half empty should report what it removed, not five
        complaints about the days that were already free.
        """
        cleared: list[date] = []
        for when in _walk(start, end):
            if self.for_date(when) and self.remove(when).success:
                cleared.append(when)
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
        return AbsenceResult(
            True, f"{removed} removed from {day.strftime('%a %-d %b')}"
        )

    # -- internals ---------------------------------------------------------

    def _has_work_in(self, day: date, portion: Portion) -> bool:
        """True when recorded work overlaps the half of the day being booked."""
        sessions = self._sessions_on(day)
        if not sessions:
            return False
        if portion is Portion.FULL:
            return True
        midday = datetime.combine(day, time(MIDDAY_HOUR, 0))
        for work in sessions:
            start = work.clock_in_event.timestamp.replace(tzinfo=None)
            end = (
                work.clock_out_event.timestamp.replace(tzinfo=None)
                if work.clock_out_event is not None
                else datetime.combine(day, time.max)
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

    # -- compatibility -----------------------------------------------------

    def mark_absence(
        self, day: date, absence_type: AbsenceType, portion: Portion = Portion.FULL
    ) -> AbsenceResult:
        """Alias of :meth:`book`, kept for callers written against the v1 name."""
        return self.book(day, absence_type, portion)

    def remove_absence(self, day: date) -> AbsenceResult:
        """Alias of :meth:`remove`, kept for callers written against the v1 name."""
        return self.remove(day)
