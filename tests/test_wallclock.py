"""The one module that reads the clock, and the pin that decides which clock.

Every expectation in this suite about a local time rests on the pin in
`tests/conftest.py`, so the pin is tested here rather than assumed. It works
through :func:`wallclock.pinned`, because ``TZ`` and :func:`time.tzset` are
POSIX-only and pin nothing on Windows.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

import pytest
import time_machine

from flexi import wallclock

LONDON = ZoneInfo("Europe/London")

MIDSUMMER = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)
"""An instant in British Summer Time: 12:00 UTC is 13:00 in London."""


def test_suite_runs_on_the_zone_it_names() -> None:
    """The machine's own zone reaches none of the suite."""
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

    London reads while the UTC task's pin is still open, and a ContextVar leaves
    each task on the zone it chose.
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


def test_reading_carries_an_offset_never_a_zone() -> None:
    """Two datetimes sharing a `ZoneInfo` subtract as wall times."""
    with wallclock.pinned(LONDON):
        moment = wallclock.local(datetime(2026, 6, 11, 9, 0))

    assert isinstance(moment.tzinfo, timezone)
    assert moment.utcoffset() == timedelta(hours=1)


def test_repeated_hour_resolves_to_the_first() -> None:
    """A naive reading is a wall reading, and `fold=0` picks the first one."""
    with wallclock.pinned(LONDON):
        moment = wallclock.local(datetime(2026, 10, 25, 1, 30))

    assert moment.utcoffset() == timedelta(hours=1)


def test_skipped_hour_resolves_to_its_instant() -> None:
    """01:30 on the March Sunday is not a time, and still has to mean one."""
    with wallclock.pinned(LONDON):
        moment = wallclock.local(datetime(2026, 3, 29, 1, 30))

    assert moment.astimezone(UTC) == datetime(2026, 3, 29, 1, 30, tzinfo=UTC)


def test_aware_moment_converts_to_the_pinned_zone() -> None:
    with wallclock.pinned(LONDON):
        moment = wallclock.local(MIDSUMMER)

    assert (moment.hour, moment.utcoffset()) == (13, timedelta(hours=1))


def test_elapsed_uses_instants_across_a_transition() -> None:
    start = datetime(2026, 10, 24, 22, 0, tzinfo=LONDON)
    end = datetime(2026, 10, 25, 6, 0, tzinfo=LONDON)

    assert wallclock.elapsed(start, end) == timedelta(hours=9)


@pytest.mark.parametrize(
    ("start", "duration", "expected_hour", "expected_minute", "expected_offset"),
    [
        (
            datetime(2026, 3, 29, 0, 30),
            timedelta(hours=7, minutes=24),
            8,
            54,
            timedelta(hours=1),
        ),
        (
            datetime(2026, 10, 25, 0, 30),
            timedelta(hours=7, minutes=24),
            6,
            54,
            timedelta(),
        ),
    ],
)
def test_advancing_elapsed_time_restores_the_destination_offset(
    start: datetime,
    duration: timedelta,
    expected_hour: int,
    expected_minute: int,
    expected_offset: timedelta,
) -> None:
    with wallclock.pinned(LONDON):
        advanced = wallclock.advance(wallclock.local(start), duration)

    assert (advanced.hour, advanced.minute) == (expected_hour, expected_minute)
    assert advanced.utcoffset() == expected_offset


def test_elapsed_operations_refuse_naive_moments() -> None:
    naive = datetime(2026, 6, 11, 9, 0)
    with pytest.raises(ValueError, match="timezone-aware"):
        wallclock.elapsed(naive, MIDSUMMER)
    with pytest.raises(ValueError, match="timezone-aware"):
        wallclock.advance(naive, timedelta(hours=1))


def test_unpinned_it_asks_the_machine() -> None:
    """The production path: no pin, and `astimezone` answers from the platform."""
    with wallclock.pinned(None):
        assert wallclock.local(MIDSUMMER) == MIDSUMMER.astimezone()
        assert wallclock.today() == datetime.now(tz=UTC).astimezone().date()


@pytest.mark.usefixtures("in_london")
def test_london_fixture_pins_without_tz() -> None:
    """`TZ` is not consulted, so a Windows runner reads the same as a Linux one."""
    with time_machine.travel(MIDSUMMER, tick=False):
        assert wallclock.now().utcoffset() == timedelta(hours=1)


class Unreadable(datetime):
    """A moment the platform's own `localtime` will not take.

    Windows raises ``OSError: [Errno 22]`` from `localtime_s` for any negative
    ``time_t``, so every moment before 1970 reads this way there.
    """

    def astimezone(self, tz: tzinfo | None = None) -> Unreadable:
        if tz is None:
            message = "Invalid argument"
            raise OSError(22, message)
        return super().astimezone(tz)


def test_unreadable_wall_time_keeps_its_day() -> None:
    """`end_of_day` manufactures one of these for every day it is asked about."""
    with wallclock.pinned(None):
        reading = wallclock.local(Unreadable(1959, 4, 6, 23, 59, 59))

    assert reading.replace(tzinfo=None) == datetime(1959, 4, 6, 23, 59, 59)
    assert reading.utcoffset() == datetime.now().astimezone().utcoffset()


def test_unreadable_instant_stays_the_same_instant() -> None:
    """Aware in, converted out: the fallback may not move the moment itself."""
    with wallclock.pinned(None):
        reading = wallclock.local(Unreadable(1959, 4, 6, 23, 59, 59, tzinfo=UTC))

    assert reading == datetime(1959, 4, 6, 23, 59, 59, tzinfo=UTC)
    assert isinstance(reading.tzinfo, timezone)
