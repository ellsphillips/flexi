"""Booking absence, and every reason a booking is refused.

The refusals matter more than the bookings: each one is a sentence the status
bar shows unedited, so a change in wording is a change in the interface.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import time_machine
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from flexi.constants import AbsenceType, Division, Portion
from flexi.domain import leaveyear
from flexi.models.database.db import (
    AbsenceDay,
    BankHolidayCache,
    BankHolidayRefresh,
    Base,
)
from flexi.models.database.engine import create_db_engine, get_session
from flexi.services.absence import (
    PLAN_CHANGED,
    AbsenceService,
    covers_the_whole_day,
    snapshot_booking,
)
from flexi.services.bank_holidays import BankHolidayService
from flexi.services.registry import build_services
from flexi.services.settings import SettingsService, SettingsUpdate, parse_settings


def _next_weekday(start: date, weekday: int) -> date:
    """Return the next date on or after start with the given weekday."""
    days_ahead = weekday - start.weekday()
    if days_ahead < 0:
        days_ahead += 7
    return start + timedelta(days=days_ahead)


@pytest.fixture
def settings(session: Session) -> SettingsService:
    svc = SettingsService(session)
    svc.save_settings(
        parse_settings(
            leave_year_start="01-01",
            working_days="0,1,2,3,4",
            bank_holiday_division="england-and-wales",
            auto_close_time="18:00",
        )
    )
    # The active leave year, not a fixed one. A hardcoded 2026 here is compared
    # against the real clock by get_active_entitlement_days, so the test would
    # have started failing on 1 January 2027 with nothing having changed.
    svc.save_entitlement(svc.active_leave_year(), 25.0)
    return svc


MIDSUMMER = datetime(2026, 6, 10, 12, 0)
"""The clock these tests run against.

Every date here is fixed, and several of them ask a question about "the active
leave year", which reads the real one. Left alone the two agree until the
calendar turns and then quietly stop: a booking on a 2026 date stops counting
against an allowance filed under 2027, and the suite fails on a morning when
nothing has changed.
"""


@pytest.fixture(autouse=True)
def _midsummer() -> Iterator[None]:
    with time_machine.travel(MIDSUMMER, tick=False):
        yield


@pytest.fixture
def bank_holidays(session: Session) -> BankHolidayService:
    now = datetime.now(tz=UTC).replace(tzinfo=None)
    session.add_all(
        (
            BankHolidayRefresh(division="england-and-wales", fetched_at=now),
            BankHolidayCache(
                division="england-and-wales",
                date=date(2026, 12, 25),
                title="Christmas Day",
            ),
        )
    )
    session.commit()
    return BankHolidayService(session, lambda: Division.ENGLAND_AND_WALES)


@pytest.fixture
def absence(
    session: Session, settings: SettingsService, bank_holidays: BankHolidayService
) -> AbsenceService:
    return AbsenceService(session, settings, bank_holidays)


# ---------- creation ----------


class TestBooking:
    def test_annual_on_working_day(self, absence: AbsenceService) -> None:
        d = _next_weekday(date(2026, 6, 8), 0)  # Monday
        result = absence.book(d, AbsenceType.ANNUAL)
        assert result.success is True

    def test_sick_on_working_day(self, absence: AbsenceService) -> None:
        d = _next_weekday(date(2026, 6, 8), 1)  # Tuesday
        result = absence.book(d, AbsenceType.SICK)
        assert result.success is True

    def test_flexi_on_working_day(self, absence: AbsenceService) -> None:
        d = _next_weekday(date(2026, 6, 8), 2)  # Wednesday
        result = absence.book(d, AbsenceType.FLEXI)
        assert result.success is True


# ---------- rejections ----------


class TestRejections:
    def test_reject_non_working_day(self, absence: AbsenceService) -> None:
        saturday = _next_weekday(date(2026, 6, 8), 5)
        result = absence.book(saturday, AbsenceType.ANNUAL)
        assert result.success is False

    def test_reject_bank_holiday(self, absence: AbsenceService) -> None:
        result = absence.book(date(2026, 12, 25), AbsenceType.ANNUAL)
        assert result.success is False

    def test_reject_duplicate(self, absence: AbsenceService) -> None:
        d = _next_weekday(date(2026, 6, 8), 0)
        absence.book(d, AbsenceType.ANNUAL)
        result = absence.book(d, AbsenceType.SICK)
        assert result.success is False

    def test_reject_when_bh_unavailable(self, tmp_path: Path) -> None:
        """It refuses rather than guess when it cannot tell if a date is a holiday.

        A fresh database, so the bank-holiday cache is genuinely empty:
        `titles_between` answers None, which is not the same as an empty mapping, and
        booking leave over a bank holiday it could not see would be worse than
        refusing.
        """
        engine = create_db_engine(tmp_path / "empty.db")
        Base.metadata.create_all(engine)
        session = get_session(engine)
        try:
            settings = SettingsService(session)
            settings.save_settings(
                parse_settings(
                    leave_year_start="01-01",
                    working_days="0,1,2,3,4",
                    bank_holiday_division="england-and-wales",
                    auto_close_time="18:00",
                )
            )
            svc = AbsenceService(
                session,
                settings,
                BankHolidayService(session, lambda: Division.ENGLAND_AND_WALES),
            )
            result = svc.book(_next_weekday(date(2026, 6, 8), 0), AbsenceType.ANNUAL)
        finally:
            session.close()
            engine.dispose()

        assert result.success is False
        assert "unavailable" in result.message

    def test_reject_on_date_with_work_session(
        self, absence: AbsenceService, session: Session
    ) -> None:
        d = _next_weekday(date(2026, 7, 6), 0)  # A Monday in future
        clock = build_services(session).clock
        now = datetime.combine(d, datetime.min.time(), tzinfo=UTC)
        clock.clock_in(now=now)
        clock.clock_out(now=now + timedelta(hours=8))

        result = absence.book(d, AbsenceType.ANNUAL)
        assert result.success is False
        assert "recorded work" in result.message

    @pytest.mark.parametrize(
        ("worked_from", "worked_to", "refused", "allowed"),
        [
            (9, 11, Portion.AM, Portion.PM),
            (14, 16, Portion.PM, Portion.AM),
        ],
    )
    def test_a_half_day_is_refused_only_over_the_half_that_was_worked(
        self,
        absence: AbsenceService,
        session: Session,
        worked_from: int,
        worked_to: int,
        refused: Portion,
        allowed: Portion,
    ) -> None:
        """`flexi leave sick today pm` is one of the command's own examples.

        `Portion.FULL` returns before any time is compared, so the whole-day
        test above never reached the comparison. Once clock events began coming
        back from `moment_of` as aware datetimes, the naive midday built beside
        them raised `TypeError: can't compare offset-naive and offset-aware`
        for every half day booked against a day with work on it.
        """
        d = _next_weekday(date(2026, 7, 6), 0)
        clock = build_services(session).clock
        midnight = datetime.combine(d, datetime.min.time(), tzinfo=UTC)
        clock.clock_in(now=midnight.replace(hour=worked_from))
        clock.clock_out(now=midnight.replace(hour=worked_to))

        over_the_work = absence.book(d, AbsenceType.SICK, portion=refused)
        assert over_the_work.success is False
        assert "recorded work" in over_the_work.message

        the_other_half = absence.book(d, AbsenceType.SICK, portion=allowed)
        assert the_other_half.success is True, the_other_half.message

    def test_other_leave_without_a_note_is_refused_and_writes_nothing(
        self, absence: AbsenceService, session: Session
    ) -> None:
        """An `Other` absence is the one whose label says nothing about the day.

        Annual, sick, TOIL and unpaid each name themselves in the records table;
        an "Other" with no note is a day off with no recoverable reason, which
        is the one absence a manager will ask about a year later.
        """
        d = _next_weekday(date(2026, 6, 8), 0)

        result = absence.book(d, AbsenceType.OTHER)

        assert result.success is False
        assert result.message == "Other absence needs a note saying what it is"
        assert absence.for_date(d) == []
        assert session.query(AbsenceDay).count() == 0

    def test_a_note_of_nothing_but_spaces_does_not_count_as_a_reason(
        self, absence: AbsenceService
    ) -> None:
        """Pressing space past the prompt is not answering it."""
        d = _next_weekday(date(2026, 6, 8), 1)

        result = absence.book(d, AbsenceType.OTHER, note="   ")

        assert result.success is False
        assert absence.for_date(d) == []

    def test_other_leave_with_a_note_is_booked(self, absence: AbsenceService) -> None:
        d = _next_weekday(date(2026, 6, 8), 2)

        result = absence.book(d, AbsenceType.OTHER, note="Jury service")

        assert result.success is True, result.message
        assert result.absence is not None
        assert result.absence.note == "Jury service"


# ---------- reading a single day ----------


class TestReadingADay:
    """What a surface drawing one date is told about it.

    A half day that reads as a whole one takes the date out of the calendar
    entirely: `covers_the_whole_day` is the answer to "may anything be worked
    here", and a morning off is not a day off. It is asked from both sides --
    the clock refuses a day that is fully booked, and `verdict_for` refuses a
    booking over a day that is fully worked.
    """

    def test_an_empty_day_is_spoken_for_by_nothing(
        self, absence: AbsenceService
    ) -> None:
        when = _next_weekday(date(2026, 6, 8), 0)

        assert absence.for_date(when) == []
        assert covers_the_whole_day(row.portion for row in absence.for_date(when)) is (
            False
        )

    def test_a_booked_morning_leaves_the_afternoon_workable(
        self, absence: AbsenceService
    ) -> None:
        when = _next_weekday(date(2026, 6, 8), 0)
        absence.book(when, AbsenceType.SICK, portion=Portion.AM)

        booked = absence.for_date(when)
        assert [row.portion for row in booked] == [Portion.AM]
        assert covers_the_whole_day(row.portion for row in booked) is False

    def test_two_halves_of_different_types_add_up_to_a_whole_day(
        self, absence: AbsenceService
    ) -> None:
        """A sick morning and an annual afternoon is a real thing that happens.

        Nothing else on the date is available to be worked, even though no row
        on it says "full day".
        """
        when = _next_weekday(date(2026, 6, 8), 0)
        absence.book(when, AbsenceType.SICK, portion=Portion.AM)
        absence.book(when, AbsenceType.ANNUAL, portion=Portion.PM)

        booked = absence.for_date(when)
        assert covers_the_whole_day(row.portion for row in booked) is True

    def test_a_full_day_is_reported_as_one_day(self, absence: AbsenceService) -> None:
        when = _next_weekday(date(2026, 6, 8), 0)
        absence.book(when, AbsenceType.ANNUAL)

        booked = absence.for_date(when)
        assert [row.absence_type for row in booked] == [AbsenceType.ANNUAL]
        assert covers_the_whole_day(row.portion for row in booked) is True


# ---------- removal ----------


class TestRemoval:
    def test_remove_existing(self, absence: AbsenceService) -> None:
        d = _next_weekday(date(2026, 6, 8), 0)
        absence.book(d, AbsenceType.SICK)
        result = absence.remove(d)
        assert result.success is True
        assert absence.for_date(d) == []

    def test_remove_nonexistent(self, absence: AbsenceService) -> None:
        result = absence.remove(date(2026, 1, 2))
        assert result.success is False

    def test_confirmed_removal_refuses_a_changed_booking(
        self, absence: AbsenceService, session: Session
    ) -> None:
        """A modal approves one immutable row, not its date-and-portion slot."""
        when = _next_weekday(date(2026, 6, 8), 0)
        created = absence.book(when, AbsenceType.ANNUAL)
        assert created.absence is not None
        confirmed = snapshot_booking(created.absence)

        created.absence.absence_type = AbsenceType.SICK
        session.commit()
        result = absence.remove_booking(confirmed)

        assert result.message == PLAN_CHANGED
        assert [row.absence_type for row in absence.for_date(when)] == [
            AbsenceType.SICK
        ]

    def test_immediate_removal_reserves_the_target_before_reading_it(
        self,
        absence: AbsenceService,
        engine: Engine,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A concurrent replacement cannot inherit the row being deleted."""
        when = _next_weekday(date(2026, 6, 8), 0)
        assert absence.book(when, AbsenceType.ANNUAL).success
        read = absence.for_date
        writer_was_blocked = False

        with get_session(engine) as competing:
            competing.connection().exec_driver_sql("PRAGMA busy_timeout=0")

            def interleave(day: date) -> list[AbsenceDay]:
                nonlocal writer_was_blocked
                rows = read(day)
                current = competing.get(AbsenceDay, rows[0].id)
                assert current is not None
                try:
                    competing.delete(current)
                    competing.commit()
                    competing.add(AbsenceDay(date=day, absence_type=AbsenceType.SICK))
                    competing.commit()
                except OperationalError:
                    competing.rollback()
                    writer_was_blocked = True
                return rows

            monkeypatch.setattr(absence, "for_date", interleave)
            result = absence.remove(when)

        assert writer_was_blocked
        assert result.success
        assert read(when) == []


# ---------- type change ----------


class TestBalance:
    def test_remaining_after_booking(self, absence: AbsenceService) -> None:
        """Asked about the leave year the day is in, not the one today is in.

        With no argument this reads the real clock, so a booking on a fixed
        2026 date stopped counting against it the moment the calendar turned.
        """
        d = _next_weekday(date(2026, 6, 8), 0)
        absence.book(d, AbsenceType.ANNUAL)

        assert absence.get_remaining_annual_leave(d) == 24.0

    def test_reject_when_insufficient(
        self,
        session: Session,
        bank_holidays: BankHolidayService,
        settings: SettingsService,
    ) -> None:
        settings.save_entitlement(2026, 1.0)
        svc = AbsenceService(session, settings, bank_holidays)
        d1 = _next_weekday(date(2026, 6, 8), 0)
        svc.book(d1, AbsenceType.ANNUAL)
        d2 = _next_weekday(date(2026, 6, 15), 1)
        result = svc.book(d2, AbsenceType.ANNUAL)
        assert result.success is False
        assert "Not enough annual leave" in result.message


# ---------- counts ----------


class TestCounts:
    def test_count_by_type(self, absence: AbsenceService) -> None:
        d1 = _next_weekday(date(2026, 6, 8), 0)
        d2 = _next_weekday(date(2026, 6, 8), 1)
        absence.book(d1, AbsenceType.SICK)
        absence.book(d2, AbsenceType.SICK)
        counted = absence.tally(d1, d2)
        assert counted[AbsenceType.SICK] == (2.0, 2)
        assert counted[AbsenceType.ANNUAL] == (0.0, 0)

    def test_counting_valid_days_only_drops_what_is_no_longer_bookable(
        self, absence: AbsenceService, settings: SettingsService
    ) -> None:
        """An allowance is spent on days that could be booked, not days that were.

        Someone books a Friday, then drops Friday from their working pattern.
        The marker stays -- it is a record of a decision -- but it no longer
        costs a day of leave, because it no longer costs a day of work.
        """
        friday = _next_weekday(date(2026, 6, 8), 4)
        assert absence.book(friday, AbsenceType.ANNUAL).success
        current = settings.resolved()
        settings.save_settings(
            SettingsUpdate(
                leave_year_start=current.leave_year_start,
                working_days=(0, 1, 2, 3),
                division=current.division,
                auto_close=current.auto_close,
            )
        )

        counted = absence.count_days(AbsenceType.ANNUAL, friday, friday)
        spent = absence.count_days(AbsenceType.ANNUAL, friday, friday, valid_only=True)

        assert counted == 1.0, "the booking is still on record"
        assert spent == 0.0, "and no longer drawn against the allowance"

    def test_remaining_allowances_for_no_years_reads_nothing(
        self, absence: AbsenceService
    ) -> None:
        """Planning an empty span asks for no years, and must not scan the table.

        The bounded read is built from the lowest and highest year requested;
        with none there is no range to build and nothing to answer.
        """
        assert absence.get_remaining_annual_leave_by_year(()) == {}

    def test_a_booking_the_pattern_no_longer_covers_is_not_charged_to_its_year(
        self, absence: AbsenceService, settings: SettingsService
    ) -> None:
        """The same rule as `valid_only`, applied where the allowance is read.

        Both sides have to agree: a day that stops counting against the balance
        has to stop counting against the entitlement, or the wallet reports
        leave spent that the planner will happily let you book again.
        """
        friday = _next_weekday(date(2026, 6, 8), 4)
        assert absence.book(friday, AbsenceType.ANNUAL).success
        year = leaveyear.active_year(friday, *settings.resolved().leave_year_start)
        before = absence.get_remaining_annual_leave_by_year((year,))[year]

        current = settings.resolved()
        settings.save_settings(
            SettingsUpdate(
                leave_year_start=current.leave_year_start,
                working_days=(0, 1, 2, 3),
                division=current.division,
                auto_close=current.auto_close,
            )
        )

        after = absence.get_remaining_annual_leave_by_year((year,))[year]
        assert before is not None
        assert after is not None
        assert after == before + 1.0, "the day came back to the allowance"

    def test_removing_one_portion_leaves_the_other_half_of_the_day(
        self, absence: AbsenceService
    ) -> None:
        """A sick morning and an annual afternoon are two bookings, not one day.

        `remove(day)` clears the date; naming a portion clears that half. With
        the filter untested, removing a morning could take the afternoon with
        it and nothing would have said so.
        """
        when = _next_weekday(date(2026, 6, 8), 0)
        assert absence.book(when, AbsenceType.SICK, Portion.AM).success
        assert absence.book(when, AbsenceType.ANNUAL, Portion.PM).success

        result = absence.remove(when, Portion.AM)

        assert result.success is True
        kept = absence.for_date(when)
        assert [(row.absence_type, row.portion) for row in kept] == [
            (AbsenceType.ANNUAL, Portion.PM)
        ]
