"""The day ledger: the one view model every widget reads.

A widget asking "was Thursday a short day?" should not have to know that the
answer involves a bank-holiday cache, a settings row, two absence rows and a
list of clock events. It asks for a :class:`DayLedger` and reads a field.

Everything here is frozen and computed. ``flexi.services.ledger`` builds these
from the database; nothing in this module touches one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from flexi.constants import AbsenceType, DayKind, Portion

MIDDAY_HOUR = 12


@dataclass(frozen=True, slots=True)
class Segment:
    """One stretch of being on the clock.

    ``end`` is ``None`` while the session is open, which is why every duration
    here takes a ``now``: a widget that redraws on a timer must be able to say
    what the elapsed time is *at the moment it is drawing*, and a segment that
    reached for the wall clock itself would make its own tests flaky.
    """

    session_id: int
    start: datetime
    end: datetime | None = None
    auto_closed: bool = False
    note: str | None = None

    @property
    def is_open(self) -> bool:
        """True while this session has no clock-out."""
        return self.end is None

    def finish(self, now: datetime) -> datetime:
        """The end of this segment, or ``now`` while it is still running."""
        return self.end if self.end is not None else now

    def duration(self, now: datetime) -> timedelta:
        """How long this segment has lasted, as at ``now``."""
        return max(timedelta(), self.finish(now) - self.start)


@dataclass(frozen=True, slots=True)
class AbsenceSlice:
    """A booked absence covering a whole day or half of one."""

    absence_id: int
    type: AbsenceType
    portion: Portion
    note: str | None = None

    @property
    def label(self) -> str:
        """How the slice names itself in a table cell."""
        if self.portion is Portion.FULL:
            return self.type.label
        return f"{self.type.label} ({self.portion.label.lower()})"

    def covers(self, moment: datetime) -> bool:
        """True when ``moment`` falls inside this slice's half of the day."""
        if self.portion is Portion.FULL:
            return True
        before_midday = moment.hour < MIDDAY_HOUR
        return before_midday if self.portion is Portion.AM else not before_midday


@dataclass(frozen=True, slots=True)
class DayLedger:
    """Everything the interface needs to know about one date."""

    date: date
    kind: DayKind
    is_working_day: bool
    contracted: timedelta
    worked: timedelta
    expected: timedelta
    toil_taken: timedelta = timedelta()
    holiday_title: str | None = None
    absences: tuple[AbsenceSlice, ...] = ()
    segments: tuple[Segment, ...] = ()

    # -- derived -----------------------------------------------------------

    @property
    def delta(self) -> timedelta:
        """Hours ahead of, or behind, what this day expected."""
        return self.worked - self.expected

    @property
    def balance_effect(self) -> timedelta:
        """What this day contributes to the running flexi balance.

        A TOIL day is a withdrawal: it expects nothing and so scores no deficit
        for being unworked, but it spends a day of the surplus that paid for it.
        """
        return self.delta - self.toil_taken

    @property
    def is_open(self) -> bool:
        """True when a session on this day is still running."""
        return any(segment.is_open for segment in self.segments)

    @property
    def is_holiday(self) -> bool:
        """True when this date is a bank holiday in the configured division."""
        return self.holiday_title is not None

    @property
    def first_in(self) -> datetime | None:
        """The earliest clock-in on this day."""
        return min((s.start for s in self.segments), default=None)

    def last_out(self, now: datetime) -> datetime | None:
        """The latest clock-out, or ``now`` if a session is still open."""
        if not self.segments:
            return None
        return max(segment.finish(now) for segment in self.segments)

    def breaks(self) -> tuple[tuple[datetime, datetime], ...]:
        """The gaps between consecutive closed sessions.

        Only gaps *between* sessions count. Time before the first clock-in and
        after the last clock-out is not a break, it is not being at work.
        """
        ordered = sorted(self.segments, key=lambda s: s.start)
        gaps: list[tuple[datetime, datetime]] = []
        for earlier, later in zip(ordered, ordered[1:], strict=False):
            if earlier.end is not None and earlier.end < later.start:
                gaps.append((earlier.end, later.start))
        return tuple(gaps)

    def break_total(self) -> timedelta:
        """How long this day's breaks lasted in total."""
        return sum(
            (end - start for start, end in self.breaks()),
            start=timedelta(),
        )

    def leave_at(self) -> datetime | None:
        """When contracted hours will have been met, given today's breaks.

        ``None`` when the day expects nothing or nobody has clocked in — there
        is no meaningful answer to "when can I go?" before you have arrived.
        """
        first = self.first_in
        if first is None or self.expected <= timedelta():
            return first
        return first + self.expected + self.break_total()

    @property
    def summary(self) -> str:
        """A single cell describing the day, for a collapsed table row."""
        if self.is_holiday:
            return self.holiday_title or "Bank holiday"
        if self.absences and not self.segments:
            return " · ".join(slice_.label for slice_ in self.absences)
        if self.absences:
            booked = " · ".join(slice_.label for slice_ in self.absences)
            return f"{booked} · worked"
        if self.segments:
            count = len(self.segments)
            return "1 session" if count == 1 else f"{count} sessions"
        if not self.is_working_day:
            return ""
        return "—"
