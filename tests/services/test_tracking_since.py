"""The gap between a leave year opening and Flexi being installed.

The days in between have no sessions on them. `tracking_since` is stamped once,
at setup, and says which of them count: below is what that does to a balance,
to a day, and what it leaves alone when the settings are edited afterwards.
"""

from __future__ import annotations

from datetime import date, time, timedelta

import time_machine
from sqlalchemy.orm import Session

from flexi.constants import AbsenceType, DayKind, Division
from flexi.models.database.db import BankHolidayCache
from flexi.services.registry import Services, build_services, invalidate_services
from flexi.services.settings import SettingsUpdate
from tests.services.conftest import CONTRACTED, Configured, work

LEAVE_YEAR_OPENED = date(2026, 4, 6)
INSTALLED = date(2026, 8, 26)
"""A Wednesday, twenty working weeks after the leave year opened."""


def balance_hours(services: Services, as_of: date) -> float:
    return services.ledger.balance(as_of).delta.total_seconds() / 3600


def test_setting_up_mid_year_is_not_a_deficit(
    configure: Configured,
) -> None:
    """An April leave year set up in August has a hundred empty days behind it."""
    with time_machine.travel(INSTALLED, tick=False):
        services = configure(leave_year_start="04-06", tracking_since=INSTALLED)

        assert balance_hours(services, INSTALLED) == -CONTRACTED.total_seconds() / 3600


def test_without_a_tracking_date_every_day_still_counts(
    configure: Configured,
) -> None:
    """`None` is the answer on a database migrated from before the column.

    Nothing dates the start of tracking there, and guessing would rewrite a
    real balance, so every day counts.
    """
    with time_machine.travel(INSTALLED, tick=False):
        services = configure(leave_year_start="04-06", tracking_since=None)

        assert balance_hours(services, INSTALLED) < -700


def test_day_before_setup_is_untracked(
    configure: Configured,
) -> None:
    """It is not a missed working day, and must not be drawn as one."""
    with time_machine.travel(INSTALLED, tick=False):
        services = configure(leave_year_start="04-06", tracking_since=INSTALLED)

        before = services.ledger.day(LEAVE_YEAR_OPENED + timedelta(days=1))
        assert before.kind is DayKind.UNTRACKED
        assert before.expected == timedelta()
        assert before.delta == timedelta(), "an untracked day moves nothing"


def test_the_setup_day_itself_is_tracked(configure: Configured) -> None:
    """The boundary is inclusive: tracking starts on the day the stamp names."""
    with time_machine.travel(INSTALLED, tick=False):
        services = configure(leave_year_start="04-06", tracking_since=INSTALLED)

        assert services.ledger.day(INSTALLED).kind is not DayKind.UNTRACKED
        assert services.ledger.day(INSTALLED).expected == CONTRACTED


def test_editing_the_settings_does_not_move_the_stamp(
    configure: Configured,
) -> None:
    """When the leave year opens and when Flexi arrived are two separate facts."""
    with time_machine.travel(INSTALLED, tick=False):
        services = configure(leave_year_start="04-06", tracking_since=INSTALLED)

    with time_machine.travel(INSTALLED + timedelta(days=60), tick=False):
        services.settings.save_settings(
            SettingsUpdate(
                leave_year_start=(1, 1),
                working_days=(0, 1, 2, 3, 4),
                division=Division.ENGLAND_AND_WALES,
                auto_close=time(18, 0),
            )
        )

        assert services.settings.resolved().tracking_since == INSTALLED


def test_setting_up_stamps_the_day_it_happened(session: Session) -> None:
    """Nothing asks the user for this date, so `save_settings` records it.

    That is the one call every route into setup goes through.
    """
    services = build_services(session)
    with time_machine.travel(INSTALLED, tick=False):
        services.settings.save_settings(
            SettingsUpdate(
                leave_year_start=(4, 6),
                working_days=(0, 1, 2, 3, 4),
                division=Division.ENGLAND_AND_WALES,
                auto_close=time(18, 0),
            )
        )

    assert services.settings.resolved().tracking_since == INSTALLED


def test_day_with_work_on_it_is_tracked(
    configure: Configured,
) -> None:
    """A recorded session is proof Flexi was there, and outranks the stamp.

    `_kind` and `expected_for` have to agree, or work against nothing expected
    reads as pure surplus.
    """
    with time_machine.travel(INSTALLED, tick=False):
        services = configure(
            leave_year_start="04-06",
            working_days="0,1,2,3,4,5,6",
            tracking_since=INSTALLED,
        )

    worked_on = INSTALLED - timedelta(days=7)
    work(services, worked_on, hours=2.0)

    day = services.ledger.day(worked_on)
    assert day.kind is not DayKind.UNTRACKED
    assert day.expected == CONTRACTED, "it is an ordinary working day after all"
    assert day.delta < timedelta(), "two hours on a seven-hour day is a shortfall"


# ---------- corrections against the stamp ----------

BEFORE_SETUP = date(2026, 6, 3)
"""A Wednesday, well inside the leave year and well before Flexi arrived."""


def banked(services: Services, as_of: date) -> timedelta:
    """The balance as a timedelta: 7.4 hours is not exact in binary floating point."""
    return services.ledger.balance(as_of).delta


def test_correcting_a_day_before_setup_banks_the_hours(
    configure: Configured,
) -> None:
    """A punched session vouches for its own day; a correction does not.

    Corrected hours went unrecorded because nothing was clocking, so the day
    still expects nothing of itself.
    """
    with time_machine.travel(INSTALLED, tick=False):
        services = configure(leave_year_start="04-06", tracking_since=INSTALLED)
        before = banked(services, INSTALLED)

        services.clock.correct(BEFORE_SETUP, time(9, 0), time(12, 30))
        invalidate_services(services)

        assert banked(services, INSTALLED) == before + timedelta(hours=3, minutes=30)


def test_full_day_corrected_before_setup_is_banked(
    configure: Configured,
) -> None:
    """A day Flexi never asked for work expects nothing, so work on it is surplus."""
    with time_machine.travel(INSTALLED, tick=False):
        services = configure(leave_year_start="04-06", tracking_since=INSTALLED)
        before = banked(services, INSTALLED)

        services.clock.correct(BEFORE_SETUP, time(9, 0), time(16, 24))
        invalidate_services(services)

        assert banked(services, INSTALLED) == before + CONTRACTED


def test_corrected_day_before_setup_expects_nothing(
    configure: Configured,
) -> None:
    """What a day expects and whether anything is known about it are two facts."""
    with time_machine.travel(INSTALLED, tick=False):
        services = configure(leave_year_start="04-06", tracking_since=INSTALLED)
        services.clock.correct(BEFORE_SETUP, time(9, 0), time(12, 30))
        invalidate_services(services)

        day = services.ledger.day(BEFORE_SETUP)
        assert day.expected == timedelta()
        assert day.kind is not DayKind.UNTRACKED, "there is work recorded on it"


def test_punch_before_setup_makes_a_working_day(
    configure: Configured,
) -> None:
    """Real events from a day make it a working day like any other."""
    with time_machine.travel(INSTALLED, tick=False):
        services = configure(leave_year_start="04-06", tracking_since=INSTALLED)
        work(services, BEFORE_SETUP, hours=7.4)

        day = services.ledger.day(BEFORE_SETUP)
        assert day.expected == CONTRACTED
        assert day.kind is DayKind.WORKING


def test_correction_after_setup_meets_the_contract(
    configure: Configured,
) -> None:
    """The carve-out stops at the stamp: past it, a half-day is a half-day."""
    tracked_day = INSTALLED - timedelta(days=1)
    with time_machine.travel(INSTALLED, tick=False):
        services = configure(leave_year_start="04-06", tracking_since=tracked_day)
        before = banked(services, INSTALLED)

        services.clock.correct(tracked_day, time(9, 0), time(12, 30))
        invalidate_services(services)

        assert banked(services, INSTALLED) == before + timedelta(hours=3, minutes=30)
        assert services.ledger.day(tracked_day).expected == CONTRACTED


# ---------- TOIL against a day nothing was expected of ----------


def test_toil_day_from_before_setup_withdraws_nothing(
    configure: Configured,
) -> None:
    """The day asked for no work, so there is no deficit for TOIL to pay off."""
    with time_machine.travel(INSTALLED, tick=False):
        services = configure(leave_year_start="04-06", tracking_since=INSTALLED)
        before = banked(services, INSTALLED)

        assert services.absence.book(BEFORE_SETUP, AbsenceType.FLEXI).success
        invalidate_services(services)

        day = services.ledger.day(BEFORE_SETUP)
        assert day.toil_taken == timedelta()
        assert day.balance_effect == timedelta()
        assert banked(services, INSTALLED) == before


def test_toil_day_the_stamp_covers_still_costs_a_day(
    configure: Configured,
) -> None:
    """The carve-out stops at the stamp, exactly as it does for a correction."""
    with time_machine.travel(INSTALLED, tick=False):
        services = configure(leave_year_start="04-06", tracking_since=LEAVE_YEAR_OPENED)

        assert services.absence.book(BEFORE_SETUP, AbsenceType.FLEXI).success
        invalidate_services(services)

        day = services.ledger.day(BEFORE_SETUP)
        assert day.toil_taken == CONTRACTED
        assert day.balance_effect == -CONTRACTED


def test_toil_on_a_new_bank_holiday_withdraws_nothing(
    configure: Configured, session: Session
) -> None:
    """The day stops expecting work, so TOIL on it pays off nothing."""
    booked = date(2026, 6, 15)
    with time_machine.travel(INSTALLED, tick=False):
        services = configure(leave_year_start="04-06", tracking_since=LEAVE_YEAR_OPENED)
        assert services.absence.book(booked, AbsenceType.FLEXI).success
        session.add(
            BankHolidayCache(
                division="england-and-wales",
                date=booked,
                title="A one-off bank holiday",
            )
        )
        session.commit()
        invalidate_services(services)

        day = services.ledger.day(booked)
        assert day.kind is DayKind.HOLIDAY
        assert day.toil_taken == timedelta()
        assert day.balance_effect == timedelta()
