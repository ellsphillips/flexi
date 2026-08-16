"""The one module that reads the clock, and the pin that decides which clock.

Every expectation in this suite about a time somebody lived rests on the pin in
`tests/conftest.py`, so the pin gets tested rather than assumed. It replaced
``TZ`` and :func:`time.tzset`, which are POSIX only and therefore pinned nothing
at all on Windows -- and a pin that silently is not one is worse than none,
because the run still carries the zone in its name.
"""

from __future__ import annotations

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


def test_the_pin_can_be_moved_and_puts_itself_back() -> None:
    """Nested, because the London fixture nests inside the autouse UTC one."""
    with time_machine.travel(MIDSUMMER, tick=False):
        with wallclock.pinned(LONDON):
            assert wallclock.now().hour == 13

        assert wallclock.now().hour == 12


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
