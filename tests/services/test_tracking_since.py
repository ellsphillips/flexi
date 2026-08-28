"""The gap between a leave year opening and Flexi being installed.

Almost nobody sets Flexi up on the first day of their leave year. The days in
between have no sessions on them, and until there was a date to say so each one
scored a full contracted day of deficit: the first user to try it opened on
-762 hours, every one of them from a day they were never asked about.

`tracking_since` is the answer, and it is stamped once, at setup. These are the
tests for what it does to a balance, what it does to a day, and what it does
*not* do when the settings are edited afterwards.
"""

from __future__ import annotations

from datetime import date, time, timedelta

import time_machine
from sqlalchemy.orm import Session

from flexi.constants import DayKind, Division
from flexi.services.registry import Services, build_services, invalidate_services
from flexi.services.settings import SettingsUpdate
from tests.services.conftest import CONTRACTED, Configured, work

LEAVE_YEAR_OPENED = date(2026, 4, 6)
INSTALLED = date(2026, 8, 26)
"""A Wednesday, twenty working weeks after the leave year opened."""


def balance_hours(services: Services, as_of: date) -> float:
    return services.ledger.balance(as_of).delta.total_seconds() / 3600


def test_setting_up_months_into_the_leave_year_is_not_a_deficit(
    configure: Configured,
) -> None:
    """The bug, in the shape a user met it.

    An April leave year set up in August is a hundred working days with nothing
    recorded against them. Counted, that is 762 hours in the red on a dashboard
    somebody has never used; the only day that should count is the one they are
    standing on.
    """
    with time_machine.travel(INSTALLED, tick=False):
        services = configure(leave_year_start="04-06", tracking_since=INSTALLED)

        assert balance_hours(services, INSTALLED) == -CONTRACTED.total_seconds() / 3600


def test_without_a_tracking_date_every_day_still_counts(
    configure: Configured,
) -> None:
    """`None` is the migrated database's answer, and it has to mean what it did.

    A database written before the column existed, with nothing recorded to date
    it by, cannot say when tracking began. Guessing would silently rewrite a
    real balance, so it counts every day exactly as it did before -- which is
    what makes this the safe backfill.
    """
    with time_machine.travel(INSTALLED, tick=False):
        services = configure(leave_year_start="04-06", tracking_since=None)

        assert balance_hours(services, INSTALLED) < -700


def test_a_day_before_setup_says_it_was_not_being_tracked(
    configure: Configured,
) -> None:
    """It is not a working day somebody missed, and must not be drawn as one."""
    with time_machine.travel(INSTALLED, tick=False):
        services = configure(leave_year_start="04-06", tracking_since=INSTALLED)

        before = services.ledger.day(LEAVE_YEAR_OPENED + timedelta(days=1))
        assert before.kind is DayKind.UNTRACKED
        assert before.expected == timedelta()
        assert before.delta == timedelta(), "an untracked day moves nothing"


def test_the_day_setup_happened_is_tracked(configure: Configured) -> None:
    """The boundary is inclusive: you are being tracked from the day you say so.

    Excluding it would lose the first day's work for anybody who set Flexi up
    and then clocked in, which is the whole of a first session.
    """
    with time_machine.travel(INSTALLED, tick=False):
        services = configure(leave_year_start="04-06", tracking_since=INSTALLED)

        assert services.ledger.day(INSTALLED).kind is not DayKind.UNTRACKED
        assert services.ledger.day(INSTALLED).expected == CONTRACTED


def test_editing_the_settings_afterwards_does_not_move_the_date(
    configure: Configured,
) -> None:
    """When the leave year starts and when Flexi arrived are two facts.

    Changing one must not restate the other. Re-stamping on every save would
    quietly wipe the history of anybody who corrected a typo in their leave year
    months later -- their whole balance, gone, with nothing said.
    """
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
    """Nothing asks the user for this date, so setup has to record it itself.

    Through `save_settings`, because that is the one call every route into
    setup goes through -- the first-run form, the settings screen and
    `flexi init` alike.
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


def test_a_day_with_work_on_it_is_tracked_whatever_the_stamp_says(
    configure: Configured,
) -> None:
    """A recorded session is proof Flexi was there, and outranks the stamp.

    The two rules have to agree. While `_kind` called such a day worked and
    `expected_for` asked nothing of it, a session before the stamp read as pure
    surplus -- two hours worked against nothing expected is +2:00 for a day that
    was four short.
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


# -- corrections against the stamp -------------------------------------------

BEFORE_SETUP = date(2026, 6, 3)
"""A Wednesday, well inside the leave year and well before Flexi arrived."""


def banked(services: Services, as_of: date) -> timedelta:
    """The balance as a timedelta.

    These are whole minutes, and 7.4 hours is not one of the numbers binary
    floating point can hold.
    """
    return services.ledger.balance(as_of).delta


def test_correcting_a_day_from_before_setup_adds_the_hours_to_the_balance(
    configure: Configured,
) -> None:
    """Remembering a morning must never cost hours, and it used to cost 3:54.

    A punched session vouches for its own day -- something clocked in, so Flexi
    was plainly running. A correction is the opposite: those hours went
    unrecorded *because* nobody was clocking. Read as a punch, it pulled a
    pre-setup day into tracking, billed it a full contracted day, and paid back
    only the half somebody could remember.
    """
    with time_machine.travel(INSTALLED, tick=False):
        services = configure(leave_year_start="04-06", tracking_since=INSTALLED)
        before = banked(services, INSTALLED)

        services.clock.correct(BEFORE_SETUP, time(9, 0), time(12, 30))
        invalidate_services(services)

        assert banked(services, INSTALLED) == before + timedelta(hours=3, minutes=30)


def test_a_full_day_corrected_from_before_setup_is_banked_rather_than_absorbed(
    configure: Configured,
) -> None:
    """The same rule a Saturday and a bank holiday already run on.

    A day Flexi never asked for work expects nothing, so work done on one is
    surplus. Anything else means a full day recovered from memory moves the
    balance by exactly zero, which reads as the feature not working.
    """
    with time_machine.travel(INSTALLED, tick=False):
        services = configure(leave_year_start="04-06", tracking_since=INSTALLED)
        before = banked(services, INSTALLED)

        services.clock.correct(BEFORE_SETUP, time(9, 0), time(16, 24))
        invalidate_services(services)

        assert banked(services, INSTALLED) == before + CONTRACTED


def test_a_corrected_day_from_before_setup_still_expects_nothing_of_itself(
    configure: Configured,
) -> None:
    """The day is no longer unknown, but it was never asked to be worked.

    Two facts that `is_tracked` used to answer with one bit: what a day expects,
    and whether anything is known about it.
    """
    with time_machine.travel(INSTALLED, tick=False):
        services = configure(leave_year_start="04-06", tracking_since=INSTALLED)
        services.clock.correct(BEFORE_SETUP, time(9, 0), time(12, 30))
        invalidate_services(services)

        day = services.ledger.day(BEFORE_SETUP)
        assert day.expected == timedelta()
        assert day.kind is not DayKind.UNTRACKED, "there is work recorded on it"


def test_a_punched_session_before_setup_still_vouches_for_its_day(
    configure: Configured,
) -> None:
    """The rule corrections are being carved out of, left standing.

    Somebody who installed Flexi, clocked in, and only later filled the stamp
    in has real events from that day, and it is a working day like any other.
    """
    with time_machine.travel(INSTALLED, tick=False):
        services = configure(leave_year_start="04-06", tracking_since=INSTALLED)
        work(services, BEFORE_SETUP, hours=7.4)

        day = services.ledger.day(BEFORE_SETUP)
        assert day.expected == CONTRACTED
        assert day.kind is DayKind.WORKING


def test_a_correction_after_setup_is_measured_against_the_contract(
    configure: Configured,
) -> None:
    """The carve-out stops at the stamp.

    Past it, a half-day is a half-day: the contract asked for a full one, and
    a correction that only accounts for part of it leaves the day behind.
    """
    tracked_day = INSTALLED - timedelta(days=1)
    with time_machine.travel(INSTALLED, tick=False):
        services = configure(leave_year_start="04-06", tracking_since=tracked_day)
        before = banked(services, INSTALLED)

        services.clock.correct(tracked_day, time(9, 0), time(12, 30))
        invalidate_services(services)

        assert banked(services, INSTALLED) == before + timedelta(hours=3, minutes=30)
        assert services.ledger.day(tracked_day).expected == CONTRACTED
