"""Pure rendering of a running session and its projected finish."""

import io
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from rich.console import Console

from flexi import wallclock
from flexi.cli.ui.onclock import on_the_clock
from flexi.constants import DayKind
from flexi.domain.ledger import DayLedger, Segment
from flexi.domain.punch import Window

LONDON = ZoneInfo("Europe/London")
CONTRACTED = timedelta(hours=7, minutes=24)


def test_half_day_finish_uses_destination_offset() -> None:
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


def test_rail_renders_with_colour() -> None:
    with wallclock.pinned(LONDON):
        since = wallclock.local(datetime(2026, 6, 10, 9, 0))
        now = wallclock.local(datetime(2026, 6, 10, 11, 30))
        ledger = DayLedger(
            date=now.date(),
            kind=DayKind.WORKING,
            is_working_day=True,
            contracted=CONTRACTED,
            worked=timedelta(hours=2, minutes=30),
            expected=CONTRACTED,
            segments=(Segment(1, since),),
        )
        stream = io.StringIO()
        console = Console(file=stream, force_terminal=True, color_system="truecolor")

        console.print(
            on_the_clock(
                ledger,
                Window.parse("07:00", "19:00"),
                since,
                timedelta(minutes=-48),
                now=now,
            ),
            soft_wrap=True,
        )

    assert "\x1b[" in stream.getvalue()
