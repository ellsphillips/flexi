"""The rail: setup drawn as a line of time with punches on it.

One continuous line down the left margin, a marker at each moment, heavy
through the step being answered and hairline through the ones already passed.
Weight, not colour, says which question is live.

Colours come from ``flexi.theme``, which parses them out of ``flexi.tcss``, so
the prompt and the application are painted from one palette. Labels stay in
default ink, which reads on a cream terminal as well as a black one.

Pure: every function returns a Rich renderable and touches no terminal.
"""

from __future__ import annotations

from enum import Enum
from typing import Final

from rich.text import Text

from flexi.theme import (
    CURSOR,
    MARK_DONE,
    MARK_GRAVE,
    MARK_LIVE,
    RAIL_LIVE,
    RAIL_SETTLED,
    TAIL,
    colour,
)

__all__ = (
    "ACTIVE",
    "ALERT",
    "GUTTER",
    "HAIRLINE",
    "HEAVY",
    "LABEL_WIDTH",
    "SETTLED",
    "Tone",
    "body",
    "measure",
    "option",
    "rail_line",
    "step",
    "tail",
    "wordmark",
)

# Local names for the design-system values, so the setup screen draws the same
# rail as the prompt that precedes it.
HEAVY: Final = RAIL_LIVE
HAIRLINE: Final = RAIL_SETTLED
ACTIVE: Final = MARK_LIVE
SETTLED: Final = MARK_DONE
ALERT: Final = MARK_GRAVE

GUTTER: Final = "  "
"""Indent to the left of the rail, so it sits off the edge of the terminal."""

LABEL_WIDTH: Final = 18
"""Where an option's hint starts, so the hints form a column."""


class Tone(Enum):
    """The four marker meanings Flexi draws."""

    LIVE = "c-accent"
    """The step being answered; the teal the application focuses with."""

    DONE = "c-surplus"
    """Settled; the green a surplus wears."""

    GRAVE = "c-deficit"
    """About to lose something; the red a deficit wears."""

    QUIET = "c-muted"
    """Structure: a settled rail, and hints."""

    @property
    def style(self) -> str:
        return colour(self.value)


def rail_line(tone: Tone) -> Text:
    """A line beginning with the rail, and nothing else styled.

    ``QUIET`` draws the hairline and every other tone draws heavy, so a section
    is live or settled by the tone it is asked for. The glyph is appended, not
    passed to the constructor: ``Text(s, style=...)`` styles the whole object,
    so everything appended afterwards would inherit the rail's colour.
    """
    line = Text(GUTTER)
    line.append(HAIRLINE if tone is Tone.QUIET else HEAVY, style=tone.style)
    return line


def wordmark() -> Text:
    """The name, once, at the top."""
    mark = Text(GUTTER)
    mark.append("⏱ ", style=Tone.LIVE.style)
    mark.append("flexi", style=f"bold {Tone.LIVE.style}")
    return mark


def step(title: str, *, tone: Tone = Tone.LIVE, marker: str = ACTIVE) -> Text:
    """A moment on the rail: its marker, then its title in plain ink."""
    line = Text(GUTTER)
    line.append(marker, style=tone.style)
    line.append("  ")
    line.append(title, style="bold")
    return line


def body(text: str = "", *, tone: Tone = Tone.QUIET, style: str = "") -> Text:
    """A line of content hanging off the rail."""
    line = rail_line(tone)
    if text:
        line.append("  ")
        line.append(text, style=style)
    return line


def measure(count: int, label: str, *, tone: Tone = Tone.QUIET) -> Text:
    """One row of a count, right-aligned so the figures form a column."""
    line = rail_line(tone)
    line.append(f"  {count:>6}  ", style="bold")
    line.append(label, style=Tone.QUIET.style)
    return line


def option(label: str, hint: str, *, picked: bool, grave: bool = False) -> Text:
    """One row of a chooser.

    The cursor carries the selection and weight confirms it, so a reader who
    cannot separate teal from grey still sees which row they are on. A row that
    destroys something is red whether or not it is picked.
    """
    tone = Tone.GRAVE if grave else Tone.LIVE
    line = rail_line(Tone.LIVE)
    line.append("  ")
    line.append(f"{CURSOR} " if picked else "  ", style=tone.style)

    if picked:
        label_style = f"bold {tone.style}"
    elif grave:
        label_style = tone.style
    else:
        label_style = ""

    line.append(label.ljust(LABEL_WIDTH) if hint else label, style=label_style)
    if hint:
        line.append(hint, style=Tone.QUIET.style)
    return line


def tail(hint: str = "") -> Text:
    """The end of the rail, carrying the keys that work here."""
    line = Text(GUTTER)
    line.append(TAIL, style=Tone.QUIET.style)
    if hint:
        line.append("  ")
        line.append(hint, style=Tone.QUIET.style)
    return line
