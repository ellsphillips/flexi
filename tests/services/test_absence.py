"""Booking absence, and every reason a booking is refused.

Each refusal is a sentence the status bar shows unedited, so a change in
wording is a change in the interface.
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

from flexi import wallclock
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
    span_of,
)
from flexi.services.bank_holidays import BankHolidayService
from flexi.services.registry import build_services
from flexi.services.settings import SettingsService, SettingsUpdate, parse_settings
from tests.conftest import sessions_on


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
    # The active leave year, not a fixed one: get_active_entitlement_days
    # compares the year it is filed under against the clock.
    svc.save_entitlement(svc.active_leave_year(), 25.0)
    return svc


MIDSUMMER = datetime(2026, 6, 10, 12, 0)
"""The clock these tests run against.

Every date here is fixed, and several of these tests ask about "the active
leave year", which reads the clock. Pinning it keeps the two in step.
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
        """Refuses when it cannot tell whether a date is a bank holiday.

        A fresh database, so the cache is empty and `titles_between` answers
        None, which is not the same as an empty mapping.
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
    def test_half_day_refused_only_over_worked_half(
        self,
        absence: AbsenceService,
        session: Session,
        worked_from: int,
        worked_to: int,
        refused: Portion,
        allowed: Portion,
    ) -> None:
        """`Portion.FULL` returns before any time is compared.

        Only a half day reaches the comparison between the aware datetimes
        `moment_of` returns and the midday built beside them.
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

    def test_other_leave_needs_a_note(
        self, absence: AbsenceService, session: Session
    ) -> None:
        """`Other` is the one type whose label says nothing about the day."""
        d = _next_weekday(date(2026, 6, 8), 0)

        result = absence.book(d, AbsenceType.OTHER)

        assert result.success is False
        assert result.message == "Other absence needs a note saying what it is"
        assert absence.for_date(d) == []
        assert session.query(AbsenceDay).count() == 0

    def test_note_of_only_spaces_is_refused(self, absence: AbsenceService) -> None:
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
    """Reading a single date, and what counts as covering it.

    `covers_the_whole_day` answers "may anything be worked here": the clock
    refuses a fully booked day, and `verdict_for` a booking over a worked one.
    """

    def test_empty_day_has_nothing_booked(self, absence: AbsenceService) -> None:
        when = _next_weekday(date(2026, 6, 8), 0)

        assert absence.for_date(when) == []
        assert covers_the_whole_day(row.portion for row in absence.for_date(when)) is (
            False
        )

    def test_booked_morning_leaves_afternoon_workable(
        self, absence: AbsenceService
    ) -> None:
        when = _next_weekday(date(2026, 6, 8), 0)
        absence.book(when, AbsenceType.SICK, portion=Portion.AM)

        booked = absence.for_date(when)
        assert [row.portion for row in booked] == [Portion.AM]
        assert covers_the_whole_day(row.portion for row in booked) is False

    def test_two_halves_add_up_to_a_whole_day(self, absence: AbsenceService) -> None:
        """No row on the date says "full day", and the date is covered."""
        when = _next_weekday(date(2026, 6, 8), 0)
        absence.book(when, AbsenceType.SICK, portion=Portion.AM)
        absence.book(when, AbsenceType.ANNUAL, portion=Portion.PM)

        booked = absence.for_date(when)
        assert covers_the_whole_day(row.portion for row in booked) is True

    def test_full_day_is_reported_as_one_day(self, absence: AbsenceService) -> None:
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

    def test_immediate_removal_reserves_its_target(
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


# ---------- what is left ----------


class TestBalance:
    def test_remaining_after_booking(self, absence: AbsenceService) -> None:
        """Asked about the leave year the day is in, not the one today is in."""
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


# ---------- an open session ----------


class TestOpenSessions:
    """How an open session weighs on a booking decision."""

    def test_afternoon_is_bookable_mid_morning(
        self, absence: AbsenceService, session: Session
    ) -> None:
        """An open session is worth what has been worked, not the whole day."""
        clock = build_services(session).clock
        with time_machine.travel(datetime(2026, 6, 10, 10, 0, tzinfo=UTC), tick=False):
            clock.clock_in(now=datetime(2026, 6, 10, 8, 30, tzinfo=UTC))

            afternoon = absence.book(MIDSUMMER.date(), AbsenceType.FLEXI, Portion.PM)
            morning = absence.book(MIDSUMMER.date(), AbsenceType.SICK, Portion.AM)

        assert afternoon.success is True, afternoon.message
        assert morning.success is False
        assert "recorded work" in morning.message

    def test_afternoon_is_not_bookable_once_worked(
        self, absence: AbsenceService, session: Session
    ) -> None:
        clock = build_services(session).clock
        with time_machine.travel(datetime(2026, 6, 10, 13, 0, tzinfo=UTC), tick=False):
            clock.clock_in(now=datetime(2026, 6, 10, 8, 30, tzinfo=UTC))

            result = absence.book(MIDSUMMER.date(), AbsenceType.FLEXI, Portion.PM)

        assert result.success is False
        assert "recorded work" in result.message

    def test_session_left_open_yesterday_covers_that_day(
        self, absence: AbsenceService, session: Session
    ) -> None:
        """Yesterday is over, so a missing clock-out is worth the rest of it."""
        yesterday = date(2026, 6, 9)
        clock = build_services(session).clock
        with time_machine.travel(datetime(2026, 6, 9, 9, 0, tzinfo=UTC), tick=False):
            clock.clock_in(now=datetime(2026, 6, 9, 8, 30, tzinfo=UTC))

        result = absence.book(yesterday, AbsenceType.FLEXI, Portion.PM)

        assert result.success is False
        assert "recorded work" in result.message

    def test_span_of_reads_the_clock_by_default(
        self, absence: AbsenceService, session: Session
    ) -> None:
        clock = build_services(session).clock
        clock.clock_in(now=datetime(2026, 6, 10, 8, 30, tzinfo=UTC))
        running = sessions_on(session, MIDSUMMER.date())[0]

        _started, ended = span_of(running)

        assert ended == wallclock.now()


# ---------- what a TOIL booking costs ----------


class TestBalanceCost:
    @pytest.fixture
    def tracked_since_january(
        self, session: Session, settings: SettingsService
    ) -> None:
        """Track from January, so every day in June is counted."""
        stored = settings.get_settings()
        assert stored is not None
        stored.tracking_since = date(2026, 1, 1)
        session.commit()

    def test_toil_for_a_day_gone_by_does_not_warn(
        self, absence: AbsenceService, tracked_since_january: None
    ) -> None:
        """A past day already scored its shortfall, so the balance does not move."""
        result = absence.book(
            date(2026, 6, 9), AbsenceType.FLEXI, available_toil_days=0.0
        )

        assert result.success is True, result.message
        assert result.warning is None

    def test_toil_for_a_day_to_come_still_warns(
        self, absence: AbsenceService, tracked_since_january: None
    ) -> None:
        """Tomorrow has not been counted yet, so booking it is a withdrawal."""
        result = absence.book(
            date(2026, 6, 11), AbsenceType.FLEXI, available_toil_days=0.0
        )

        assert result.success is True, result.message
        assert (
            result.warning
            == "Booked, but this takes the flexi balance 1 day into deficit"
        )


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

    def test_valid_only_drops_unbookable_days(
        self, absence: AbsenceService, settings: SettingsService
    ) -> None:
        """A day dropped from the pattern keeps its marker and costs no leave."""
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

    def test_no_years_asked_reads_nothing(self, absence: AbsenceService) -> None:
        """The bounded read is built from the lowest and highest year asked for."""
        assert absence.get_remaining_annual_leave_by_year(()) == {}

    def test_unbookable_day_is_not_charged_to_its_year(
        self, absence: AbsenceService, settings: SettingsService
    ) -> None:
        """The balance and the entitlement have to drop the same day."""
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

    def test_removing_one_portion_keeps_the_other(
        self, absence: AbsenceService
    ) -> None:
        """`remove(day)` clears the date; naming a portion clears that half."""
        when = _next_weekday(date(2026, 6, 8), 0)
        assert absence.book(when, AbsenceType.SICK, Portion.AM).success
        assert absence.book(when, AbsenceType.ANNUAL, Portion.PM).success

        result = absence.remove(when, Portion.AM)

        assert result.success is True
        kept = absence.for_date(when)
        assert [(row.absence_type, row.portion) for row in kept] == [
            (AbsenceType.ANNUAL, Portion.PM)
        ]
