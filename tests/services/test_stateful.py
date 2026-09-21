"""A model of the clock and the absence book, driven against the real services.

Hypothesis chooses the sequence, and after every step the database is checked
against a simple model. The clock's rules are few, so the model predicts them
and a disagreement is a failure. The absence rules are many (working days, bank
holidays, entitlement, clashes, notes), so absences are observed instead: the
model records what the service reported and the invariants hold either way.
"""

from __future__ import annotations

import shutil
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest
import time_machine
from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
)
from sqlalchemy import func, select

from flexi.constants import AbsenceType, Portion
from flexi.models.database.db import (
    AbsenceDay,
    BankHolidayCache,
    BankHolidayRefresh,
    WorkSession,
)
from flexi.models.database.engine import create_db_engine, get_session
from flexi.models.database.moment import moment_of
from flexi.services.absence import covers_the_whole_day
from flexi.services.registry import build_services, invalidate_services
from flexi.services.settings import parse_settings
from tests import strategies
from tests.database import create_schema

START = datetime(2026, 6, 1, 8, 0)
"""A Monday morning, early in a leave year that starts on 6 April."""

AUTO_CLOSE = time(18, 0)
HOLIDAY = date(2026, 6, 2)
"""A bank holiday on the Tuesday of the first week.

Inside the eight days the clock may wander, so the "cannot clock in on a bank
holiday" branch is one the model reaches.
"""

DAYS = 8
"""How far a single run may wander. Long enough to cross a weekend and to leave
a session open overnight; short enough that shrinking a failure stays readable."""


class TimesheetModel(RuleBasedStateMachine):
    """Clock actions and absence bookings, in any order Hypothesis likes."""

    def __init__(self) -> None:
        super().__init__()
        self.now = START
        self.open_since: datetime | None = None
        self.sessions: list[tuple[datetime, datetime, bool]] = []
        """Every closed session: opened, closed, and whether it was voided."""
        self.observed_absences: dict[date, dict[Portion, AbsenceType]] = {}

    @initialize()
    def open_the_database(self) -> None:
        """One empty database per run, configured as a first run leaves it."""
        self.directory = tempfile.mkdtemp()
        path = Path(self.directory) / "model.db"
        self.engine = create_db_engine(path)
        create_schema(self.engine)
        self.db = get_session(self.engine)

        build_services(self.db).settings.save_settings(
            parse_settings(
                leave_year_start="04-06",
                working_days="0,1,2,3,4",
                bank_holiday_division="england-and-wales",
                auto_close_time=AUTO_CLOSE.strftime("%H:%M"),
            )
        )
        self.db.add_all(
            (
                BankHolidayRefresh(
                    division="england-and-wales",
                    fetched_at=datetime(2026, 1, 1, 9, 0),
                ),
                BankHolidayCache(
                    division="england-and-wales",
                    date=HOLIDAY,
                    title="Bank holiday",
                ),
            )
        )
        self.db.commit()
        self.services = build_services(self.db)
        self.services.settings.save_entitlement(
            self.services.settings.active_leave_year(), 25.0
        )
        self.minimum = self.services.clock._minimum

    def teardown(self) -> None:
        """Give the database back, and the file it lived in.

        Closing the session returns its connection to the pool and leaves the
        handle open. Windows will not delete a file that is open, and shrinking
        a failure replays the example many times over.
        """
        # A run of zero steps never reaches `@initialize`, and an AttributeError
        # escaping teardown is reported as a flaky strategy.
        if not hasattr(self, "db"):
            return
        self.db.close()
        self.engine.dispose()
        # A directory that will not go is worth neither failing the example
        # nor reporting as flakiness.
        shutil.rmtree(self.directory, ignore_errors=True)

    # The passage of time

    @rule(minutes=st.integers(min_value=1, max_value=11 * 60))
    def time_passes(self, minutes: int) -> None:
        """The only rule that moves the clock, and it only moves it forward."""
        moved = self.now + timedelta(minutes=minutes)
        if moved < START + timedelta(days=DAYS):
            self.now = moved

    # The clock, predicted

    def _sweep(self) -> None:
        """What `run_startup_cleanup` will do, before it does it.

        A session left open on an earlier date is closed at the configured time,
        or at 23:59 when that time has already passed by the time it was opened.
        """
        if self.open_since is None or self.open_since.date() >= self.now.date():
            return
        closing = AUTO_CLOSE if self.open_since.time() <= AUTO_CLOSE else time(23, 59)
        closed_at = datetime.combine(self.open_since.date(), closing)
        closed_at = self._first_booked_moment(closed_at) or closed_at
        self.sessions.append((self.open_since, closed_at, False))
        self.open_since = None

    def _first_booked_moment(self, until: datetime) -> datetime | None:
        """The first occupied half-day that intersects the running interval."""
        if self.open_since is None:
            return None
        for day, booked in sorted(self.observed_absences.items()):
            for half, hour in ((Portion.AM, 0), (Portion.PM, 12)):
                start = datetime.combine(day, time(hour))
                end = start + timedelta(hours=12)
                if (
                    (Portion.FULL in booked or half in booked)
                    and start < until
                    and self.open_since < end
                ):
                    return max(self.open_since, start)
        return None

    def _booked_over(self, moment: datetime) -> bool:
        """Whether an absence booked on a date is spoken for at this moment.

        A booked morning leaves the afternoon workable, so only a moment inside
        the half that is booked is refused. Noon starts the afternoon.
        """
        booked = self.observed_absences.get(moment.date(), {})
        if covers_the_whole_day(booked):
            return True
        if Portion.AM in booked and moment.time() < time(12, 0):
            return True
        return Portion.PM in booked and moment.time() >= time(12, 0)

    @rule()
    def clock_in(self) -> None:
        with time_machine.travel(self.now, tick=False):
            result = self.services.clock.clock_in()

        self._sweep()
        booked = self.observed_absences.get(self.now.date(), {})
        expected = (
            self.open_since is None
            and self.now.date() != HOLIDAY
            and not self._booked_over(self.now)
        )
        assert result.success is expected, (
            f"clock in at {self.now} with open={self.open_since} "
            f"booked={sorted(p.value for p in booked)}: {result.message}"
        )
        if result.success:
            self.open_since = self.now

    @rule()
    def clock_out(self) -> None:
        with time_machine.travel(self.now, tick=False):
            result = self.services.clock.clock_out()

        expected = self.open_since is not None and (
            self.now - self.open_since < self.minimum
            or self._first_booked_moment(self.now) is None
        )
        assert result.success is expected, result.message
        if result.success and self.open_since is not None:
            length = self.now - self.open_since
            self.sessions.append((self.open_since, self.now, length < self.minimum))
            self.open_since = None

    # The absence book, observed

    @rule(
        offset=st.integers(min_value=0, max_value=DAYS),
        kind=st.sampled_from([AbsenceType.ANNUAL, AbsenceType.SICK, AbsenceType.FLEXI]),
        portion=strategies.portions,
    )
    def book(self, offset: int, kind: AbsenceType, portion: Portion) -> None:
        """Book a day off, and believe what the service says about it."""
        when = START.date() + timedelta(days=offset)
        with time_machine.travel(self.now, tick=False):
            self.services.absence.book(when, kind, portion)
        self._reread(when)
        invalidate_services(self.services)

    @rule(offset=st.integers(min_value=0, max_value=DAYS))
    def remove(self, offset: int) -> None:
        when = START.date() + timedelta(days=offset)
        with time_machine.travel(self.now, tick=False):
            self.services.absence.remove(when)
        self._reread(when)
        invalidate_services(self.services)

    def _reread(self, when: date) -> None:
        """Take what is booked on a date from the service, not from a guess.

        Restating the refusal rules here would be a second implementation to
        keep in step with the first.
        """
        rows = {
            row.portion: row.absence_type
            for row in self.services.absence.for_date(when)
        }
        if rows:
            self.observed_absences[when] = rows
        else:
            self.observed_absences.pop(when, None)

    # What must be true after every step

    @precondition(lambda self: hasattr(self, "db"))
    @invariant()
    def only_ever_one_session_is_open(self) -> None:
        """Two open sessions means every duration from here is a guess."""
        open_rows = self.db.execute(
            select(func.count())
            .select_from(WorkSession)
            .where(WorkSession.clock_out_id.is_(None))
        ).scalar_one()
        assert open_rows <= 1

    @precondition(lambda self: hasattr(self, "services"))
    @invariant()
    def the_clock_agrees_with_the_model(self) -> None:
        assert self.services.clock.is_clocked_in() is (self.open_since is not None)

    @precondition(lambda self: hasattr(self, "db"))
    @invariant()
    def a_full_day_never_coexists_with_a_half(self) -> None:
        """The rule SQLite cannot express, which is why the service must."""
        rows = self.db.execute(select(AbsenceDay)).scalars().all()
        by_day: dict[date, set[Portion]] = {}
        for row in rows:
            by_day.setdefault(row.date, set()).add(row.portion)
        for when, portions in by_day.items():
            assert not (Portion.FULL in portions and len(portions) > 1), (
                f"{when} holds {sorted(p.value for p in portions)}"
            )

    @precondition(lambda self: hasattr(self, "db"))
    @invariant()
    def no_session_runs_backwards(self) -> None:
        for row in self.db.execute(select(WorkSession)).scalars():
            if row.clock_out_event is None:
                continue
            assert moment_of(row.clock_out_event) >= moment_of(row.clock_in_event)

    @precondition(lambda self: hasattr(self, "db"))
    @invariant()
    def the_recorded_work_is_the_work_the_model_did(self) -> None:
        """Every minute the model clocked is a minute the database holds."""
        recorded = timedelta()
        for row in self.db.execute(select(WorkSession)).scalars():
            if row.voided or row.clock_out_event is None:
                continue
            recorded += moment_of(row.clock_out_event) - moment_of(row.clock_in_event)

        modelled = sum(
            (end - start for start, end, voided in self.sessions if not voided),
            timedelta(),
        )
        assert recorded == modelled, f"{recorded} recorded, {modelled} modelled"


TestTimesheetModel = TimesheetModel.TestCase
TestTimesheetModel.settings = settings(
    max_examples=40,
    stateful_step_count=40,
    deadline=None,
    suppress_health_check=[HealthCheck.data_too_large, HealthCheck.too_slow],
)
"""This test sets its own budget instead of following the profile.

One example is a fresh SQLite database and up to forty service calls against
it. Depth per example is worth more here than breadth across them, which is
what `stateful_step_count` buys.
"""

TestTimesheetModel.pytestmark = [pytest.mark.timeout(300)]
"""A budget of its own, because the global one is not meant for this test.

`--timeout=120` is sized for tests that take milliseconds and is enforced by a
thread calling `os._exit`, which under xdist kills the worker and is reported
as "node down" against whichever test it was running. Shrinking a failure here
replays the example many times over, and five minutes is still a bound.
"""
