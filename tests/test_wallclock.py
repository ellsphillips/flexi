"""The one module that reads the clock, and the pin that decides which clock.

Every expectation in this suite about a time somebody lived rests on the pin in
`tests/conftest.py`, so the pin gets tested rather than assumed. It replaced
``TZ`` and :func:`time.tzset`, which are POSIX only and therefore pinned nothing
at all on Windows -- and a pin that silently is not one is worse than none,
because the run still carries the zone in its name.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
import time_machine

from flexi import wallclock

LONDON = ZoneInfo("Europe/London")

MIDSUMMER = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)
"""An instant in British Summer Time: 12:00 UTC is 13:00 in London."""


def test_the_suite_runs_on_the_zone_it_says_it_does() -> None:
    """Whatever the machine underneath is set to.

    This is the assertion the timezone matrix rests on. Both rows run the same
    suite; what the `Europe/London` row proves is that the machine's own zone
    reaches none of it.
    """
    with time_machine.travel(MIDSUMMER, tick=False):
        assert wallclock.now() == datetime(2026, 6, 11, 12, 0, tzinfo=UTC)


def test_nested_pins_restore_the_zone_at_each_level() -> None:
    """The London fixture nests inside the autouse UTC pin in the same way."""
    with time_machine.travel(MIDSUMMER, tick=False):
        with wallclock.pinned(LONDON):
            assert wallclock.now().hour == 13
            with wallclock.pinned(UTC):
                assert wallclock.now().hour == 12
            assert wallclock.now().hour == 13

        assert wallclock.now().hour == 12


def test_utc_readings_do_not_take_the_wall_time_pin() -> None:
    with time_machine.travel(MIDSUMMER, tick=False), wallclock.pinned(LONDON):
        assert wallclock.utc_now() == MIDSUMMER


async def test_overlapping_tasks_cannot_move_each_others_pin() -> None:
    """A pin belongs to an execution context, not to the whole process.

    London reads while the UTC task's pin is still open. With mutable global
    state that second pin moves both tasks to UTC; a ContextVar leaves each task
    on the zone it chose.
    """
    london_ready = asyncio.Event()
    utc_ready = asyncio.Event()
    london_read = asyncio.Event()

    async def read_london() -> int:
        with wallclock.pinned(LONDON):
            london_ready.set()
            await utc_ready.wait()
            hour = wallclock.local(MIDSUMMER).hour
            london_read.set()
            return hour

    async def read_utc() -> int:
        await london_ready.wait()
        with wallclock.pinned(UTC):
            utc_ready.set()
            await london_read.wait()
            return wallclock.local(MIDSUMMER).hour

    london_hour, utc_hour = await asyncio.gather(read_london(), read_utc())

    assert (london_hour, utc_hour) == (13, 12)


def test_a_reading_carries_a_number_and_never_a_zone() -> None:
    """The whole design of the module, and the pin must not undo it.

    Two datetimes sharing a `ZoneInfo` subtract as wall times and lose a
    transition. A pinned reading has to come back in the same shape an unpinned
    one does, or the pin quietly reintroduces the bug the module exists to
    prevent.
    """
    with wallclock.pinned(LONDON):
        moment = wallclock.local(datetime(2026, 6, 11, 9, 0))

    assert isinstance(moment.tzinfo, timezone)
    assert moment.utcoffset() == timedelta(hours=1)


def test_the_hour_that_happens_twice_resolves_to_the_first_of_them() -> None:
    """A naive reading is a wall reading, and 01:30 that morning is two of them.

    `fold=0` is what the unpinned reader gives, so it is what the pinned one
    has to give.
    """
    with wallclock.pinned(LONDON):
        moment = wallclock.local(datetime(2026, 10, 25, 1, 30))

    assert moment.utcoffset() == timedelta(hours=1)


def test_the_hour_that_never_happens_resolves_to_the_instant_it_names() -> None:
    """01:30 on the March Sunday is not a time. It still has to mean something."""
    with wallclock.pinned(LONDON):
        moment = wallclock.local(datetime(2026, 3, 29, 1, 30))

    assert moment.astimezone(UTC) == datetime(2026, 3, 29, 1, 30, tzinfo=UTC)


def test_an_aware_moment_is_converted_to_the_pinned_zone() -> None:
    with wallclock.pinned(LONDON):
        moment = wallclock.local(MIDSUMMER)

    assert (moment.hour, moment.utcoffset()) == (13, timedelta(hours=1))


def test_unpinned_it_asks_the_machine() -> None:
    """The production path: no pin, and `astimezone` answers from the platform.

    Asserted against the platform rather than against a zone, because what is
    being checked is that nothing is pinned -- the answer is whatever the
    machine running this says, which is the point.
    """
    with wallclock.pinned(None):
        assert wallclock.local(MIDSUMMER) == MIDSUMMER.astimezone()
        assert wallclock.today() == datetime.now(tz=UTC).astimezone().date()


@pytest.mark.usefixtures("in_london")
def test_the_london_fixture_is_the_pin_and_not_the_environment() -> None:
    """`TZ` is not consulted, so a Windows runner reads the same as a Linux one."""
    with time_machine.travel(MIDSUMMER, tick=False):
        assert wallclock.now().utcoffset() == timedelta(hours=1)
