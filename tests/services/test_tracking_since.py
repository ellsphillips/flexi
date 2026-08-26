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

from datetime import date, timedelta

import time_machine
from sqlalchemy.orm import Session

from flexi.constants import DayKind
from flexi.services.registry import Services
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
            leave_year_start="01-01",
            working_days="0,1,2,3,4",
            bank_holiday_division="england-and-wales",
            auto_close_time="18:00",
        )

        assert services.settings.get_tracking_since() == INSTALLED


def test_setting_up_stamps_the_day_it_happened(session: Session) -> None:
    """Nothing asks the user for this date, so setup has to record it itself.

    Through `save_settings`, because that is the one call every route into
    setup goes through -- the first-run form, the settings screen and
    `flexi init` alike.
    """
    services = Services.build(session)
    with time_machine.travel(INSTALLED, tick=False):
        services.settings.save_settings(
            leave_year_start="04-06",
            working_days="0,1,2,3,4",
            bank_holiday_division="england-and-wales",
            auto_close_time="18:00",
        )

    assert services.settings.get_tracking_since() == INSTALLED


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
