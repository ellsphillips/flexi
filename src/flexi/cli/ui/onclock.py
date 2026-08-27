"""A running session, drawn on the rail.

`flexi clock in` while already on the clock answered "Already clocked in" and
stopped, which withholds the thing actually being asked: since when, and how am
I doing. Pure -- returns a Rich renderable and touches no terminal.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Final

from rich.text import Text

from flexi import wallclock
from flexi.cli.ui.rail import GUTTER, HAIRLINE, SETTLED, Tone
from flexi.domain.format import clock, delta, hm
from flexi.domain.ledger import DayLedger
from flexi.domain.punch import Window, strip
from flexi.theme import CELL_GLYPHS, TAIL, colour

__all__ = (
    "CELL_TONES",
    "STRIP_CELLS",
    "elapsed_since",
    "on_the_clock",
    "punch_line",
)

STRIP_CELLS = 44
"""Fixed, not measured: a pure function cannot ask the terminal its width, and
this leaves both window labels room inside eighty columns."""

CELL_TONES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "off": "c-line",
        "break": "c-muted",
        "target": "c-accent",
        "absence": "c-annual",
        "holiday": "c-muted",
        "on": "c-surplus",
        "live": "c-accent-lift",
    }
)


def punch_line(ledger: DayLedger, window: Window, *, now: datetime) -> Text:
    """The day as a row of cells, in the palette the dashboard uses."""
    line = Text(no_wrap=True)
    for cell in strip(ledger, STRIP_CELLS, window, now=now):
        line.append(CELL_GLYPHS[cell], style=colour(CELL_TONES[cell.value]))
    return line


def elapsed_since(since: datetime, now: datetime) -> timedelta:
    """How long the session has run.

    Clamped, so a clock corrected under it cannot read "-0:04 so far".
    """
    return max(timedelta(), wallclock.elapsed(since, now))


def on_the_clock(
    ledger: DayLedger,
    window: Window,
    since: datetime,
    balance: timedelta,
    *,
    now: datetime,
) -> Text:
    """Since when, how long, the strip, and where it leaves the balance.

    ``since`` is passed rather than taken off the ledger, which knows when the
    day's first session opened -- a different moment once lunch has happened.
    """
    line = colour("c-line")
    muted = colour("c-muted")
    paper = colour("c-paper")
    remaining = max(timedelta(), ledger.expected - ledger.worked)

    facts = Text(f"in at {clock(since)}", style=paper)
    facts.append("  ·  ", style=line)
    facts.append(f"{hm(elapsed_since(since, now))} on this session", style=muted)

    tally = Text(f"{hm(ledger.worked)} of {hm(ledger.expected)} today", style=paper)
    tally.append("  ·  ", style=line)
    if remaining:
        tally.append(
            f"hours met at {clock(wallclock.advance(now, remaining))}", style=muted
        )
    else:
        tally.append("hours met", style=colour("c-surplus"))

    scale = Text(f"{window.start:%H:%M} ", style=line)
    scale.append_text(punch_line(ledger, window, now=now))
    scale.append(f" {window.end:%H:%M}", style=line)

    standing = Text("balance ", style=muted)
    standing.append(
        delta(balance),
        style=colour("c-surplus" if balance >= timedelta() else "c-deficit"),
    )

    rows: list[tuple[str, Tone, Text | str]] = [
        (SETTLED, Tone.DONE, Text("Already on the clock", style=f"bold {paper}")),
        (HAIRLINE, Tone.QUIET, ""),
        (HAIRLINE, Tone.QUIET, facts),
        (HAIRLINE, Tone.QUIET, tally),
        (HAIRLINE, Tone.QUIET, ""),
        (HAIRLINE, Tone.QUIET, scale),
        (HAIRLINE, Tone.QUIET, ""),
        (TAIL, Tone.QUIET, standing),
    ]

    block = Text(no_wrap=True)
    for index, (glyph, tone, body) in enumerate(rows):
        if index:
            block.append("\n")
        block.append(GUTTER)
        block.append(glyph, style=tone.style)
        if isinstance(body, str):
            continue
        block.append("  ")
        block.append_text(body)
    return block
