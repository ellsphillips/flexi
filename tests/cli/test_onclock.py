"""Pure rendering of a running session and its projected finish."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flexi import wallclock
from flexi.cli.ui.onclock import on_the_clock
from flexi.constants import DayKind
from flexi.domain.ledger import DayLedger, Segment
from flexi.domain.punch import Window

LONDON = ZoneInfo("Europe/London")
CONTRACTED = timedelta(hours=7, minutes=24)


def test_reduced_projection_uses_expected_time_and_destination_offset() -> None:
    """A half day crossing spring forward finishes at 05:12 BST, not 04:12 GMT."""
    with wallclock.pinned(LONDON):
        now = wallclock.local(datetime(2026, 3, 29, 0, 30))
        expected = CONTRACTED / 2
        ledger = DayLedger(
            date=now.date(),
            kind=DayKind.WORKING,
            is_working_day=True,
            contracted=CONTRACTED,
            worked=timedelta(),
            expected=expected,
            segments=(Segment(1, now),),
        )

        rendered = on_the_clock(
            ledger,
            Window.parse("00:00", "12:00"),
            now,
            timedelta(),
            now=now,
        )

    assert "0:00 of 3:42 today" in rendered.plain
    assert "hours met at 05:12" in rendered.plain
