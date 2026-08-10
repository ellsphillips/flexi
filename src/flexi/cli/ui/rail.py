"""The rail: setup drawn as a line of time with punches on it.

Flexi draws a working day as a strip of cells, filled where you were on the
clock. The rail is that idea stood on its end -- one continuous line down the
left margin, a marker at each moment, **heavy through the step being answered
and hairline through the ones already passed**. The weight is not decoration; it
is the only thing that says which question is live, which is why the rail can
carry the whole flow without drawing a single box.

Colours come from ``flexi.theme``, which parses them out of ``flexi.tcss``, so
the prompt and the application are painted from one palette. Text itself stays
in default ink: a coloured marker beside an uncoloured label reads on a cream
terminal as well as a black one, and a prompt is a bad place to discover
somebody's background is not what you assumed. The accent is spent on the rail
and the cursor, and nowhere else.

Pure. Every function returns a Rich renderable and touches no terminal.
"""

from __future__ import annotations

from enum import Enum
from typing import Final

from rich.text import Text

from flexi.theme import colour

HEAVY: Final = "┃"
"""The rail through the live step."""

HAIRLINE: Final = "│"
"""The rail through everything settled."""

TAIL: Final = "╰"
ACTIVE: Final = "◆"
SETTLED: Final = "●"
ALERT: Final = "▲"
CURSOR: Final = "▸"

GUTTER: Final = "  "
"""Indent to the left of the rail, so it sits off the edge of the terminal."""

LABEL_WIDTH: Final = 18
"""Where an option's hint starts, so the hints form a column."""


class Tone(Enum):
    """What a marker means, in the only four flavours Flexi needs."""

    LIVE = "c-accent"
    """The step being answered. The teal the application focuses with."""

    DONE = "c-surplus"
    """Settled. The green a surplus wears."""

    GRAVE = "c-deficit"
    """About to lose something. The red a deficit wears."""

    QUIET = "c-muted"
    """Structure rather than content: a settled rail, and hints."""

    @property
    def style(self) -> str:
        return colour(self.value)


def _rail(tone: Tone) -> Text:
    """A line beginning with the rail, and nothing else styled.

    ``QUIET`` draws the hairline and everything else draws heavy, so a section
    is live or settled by virtue of the tone it is asked for. That is what lets
    the destructive step run heavy *red* down its whole height rather than
    wearing the accent, which would be the wrong promise entirely.

    The glyph is appended rather than passed to the constructor. ``Text(s,
    style=...)`` styles the whole object, so everything appended afterwards
    inherits it -- which paints the labels in the accent and leaves the rail
    saying nothing, because it says it everywhere.
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
    line = _rail(tone)
    if text:
        line.append("  ")
        line.append(text, style=style)
    return line


def measure(count: int, label: str, *, tone: Tone = Tone.QUIET) -> Text:
    """One row of a count, right-aligned so the figures form a column."""
    line = _rail(tone)
    line.append(f"  {count:>6}  ", style="bold")
    line.append(label, style=Tone.QUIET.style)
    return line


def option(label: str, hint: str, *, picked: bool, grave: bool = False) -> Text:
    """One row of a chooser.

    The cursor carries the selection and weight confirms it, so somebody who
    cannot separate teal from grey still sees which row they are on. A row that
    destroys something is red whether or not it is picked: finding that out by
    landing on it is one keystroke too late to be useful.
    """
    tone = Tone.GRAVE if grave else Tone.LIVE
    line = _rail(Tone.LIVE)
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
