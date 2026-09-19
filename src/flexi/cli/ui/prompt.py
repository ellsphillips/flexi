"""The one module in ``flexi.cli.ui`` that owns a terminal.

Raw mode, the byte reads and the cursor arithmetic live here; everything else
in the package is a pure function of its arguments.

Drawing goes to stderr, the stream :func:`interactive` checks, so the guard and
the writes look at the same end. Lines are cropped, never wrapped: a rewind is
line arithmetic, and a wrapped line makes the redraw eat the line above it.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from types import MappingProxyType
from typing import Final

from rich.console import Console
from rich.text import Text

from flexi.cli.ui import rail
from flexi.cli.ui.keys import Key, decode, incomplete
from flexi.cli.ui.menu import Menu, Option

__all__ = (
    "ESCAPE_WAIT",
    "WINDOWS_PREFIXES",
    "WINDOWS_SCANCODES",
    "Surface",
    "abandon",
    "choose",
    "console",
    "interactive",
    "more_coming",
    "read_key",
    "read_posix",
    "read_windows",
    "type_the_word",
    "unbuffered",
    "write",
)

ESCAPE_WAIT: Final = 0.05
"""Seconds to wait for the rest of an escape sequence.

Long enough to cross a slow link, short enough that a lone escape still
answers at once.
"""


def console() -> Console:
    """A console pointed at stderr, with no colour guessing."""
    return Console(stderr=True, highlight=False, markup=False, emoji=False)


def interactive() -> bool:
    """True when there is a terminal to draw on and a reader to answer.

    Both ends are checked: stdin for the answer, stderr for the drawing.
    """
    return sys.stdin.isatty() and sys.stderr.isatty()


# Reading --------------------------------------------------------------------


@contextmanager
def unbuffered() -> Iterator[int]:
    """Put the terminal into cbreak, so keys arrive as they are struck.

    ``cbreak`` and not ``raw``: it leaves signal handling on, so ctrl-c still
    interrupts. The saved mode is restored however the block is left. Windows
    needs no mode at all, because ``msvcrt.getwch`` reads a character straight
    off the console, unbuffered and unechoed.
    """
    if sys.platform == "win32":  # pragma: no cover - POSIX takes the branch below
        yield sys.stdin.fileno()
        return

    import termios
    import tty

    descriptor = sys.stdin.fileno()
    saved = termios.tcgetattr(descriptor)
    try:
        tty.setcbreak(descriptor)
        yield descriptor
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, saved)


def more_coming(descriptor: int) -> bool:
    import select

    ready, _, _ = select.select([descriptor], [], [], ESCAPE_WAIT)
    return bool(ready)


def read_posix(descriptor: int) -> Key:
    sequence = os.read(descriptor, 1).decode("utf-8", errors="ignore")
    while incomplete(sequence) and more_coming(descriptor):
        sequence += os.read(descriptor, 1).decode("utf-8", errors="ignore")
    return decode(sequence)


WINDOWS_PREFIXES: Final = ("\x00", "\xe0")
"""The bytes the Windows console sends ahead of a scan code."""

WINDOWS_SCANCODES: Final[Mapping[str, Key]] = MappingProxyType(
    {"H": Key.UP, "P": Key.DOWN}
)
"""The scan codes for the two keys a menu moves on."""


def read_windows(getwch: Callable[[], str]) -> Key:
    """One key press, as the Windows console delivers it.

    An arrow comes as two reads: a prefix saying a scan code follows, then the
    code. There is no escape sequence, so nothing has to be waited for and
    escape is never ambiguous. Everything else arrives whole and is named by
    the same table POSIX uses.
    """
    first = getwch()
    if first in WINDOWS_PREFIXES:
        return WINDOWS_SCANCODES.get(getwch(), Key.UNKNOWN)
    return decode(first)


def read_key(descriptor: int) -> Key:
    """One key press, as this platform delivers it."""
    if sys.platform == "win32":  # pragma: no cover - POSIX takes the branch below
        import msvcrt

        return read_windows(msvcrt.getwch)
    return read_posix(descriptor)


# Drawing --------------------------------------------------------------------


class Surface:
    """Lines drawn to the terminal, and the means to take them back.

    Holds the count of what it last drew so it can rewind exactly that far.
    Cropping every line, never wrapping, is what keeps the count right.
    """

    def __init__(self, out: Console | None = None) -> None:
        self._console = out or console()
        self._drawn = 0

    def draw(self, lines: Sequence[Text]) -> None:
        for line in lines:
            self._console.print(line, no_wrap=True, overflow="crop")
        self._drawn = len(lines)

    def rewind(self) -> None:
        """Put the cursor back where the last draw started, and clear.

        A console with no virtual-terminal processing prints the escape
        sequence instead of obeying it, and this write is the one place that
        goes past Rich's legacy renderer, so there the rewind is skipped and
        the frames stack.
        """
        if self._drawn and not self._console.legacy_windows:
            self._console.file.write(f"\x1b[{self._drawn}F\x1b[0J")
            self._console.file.flush()
        self._drawn = 0

    def redraw(self, lines: Sequence[Text]) -> None:
        self.rewind()
        self.draw(lines)

    def draw_open(self, line: Text) -> None:
        """Draw a line and leave the cursor on it, for typing into."""
        self._console.print(line, no_wrap=True, overflow="crop", end="")

    @contextmanager
    def without_cursor(self) -> Iterator[None]:
        self._console.show_cursor(False)
        try:
            yield
        finally:
            self._console.show_cursor(True)


def write(lines: Sequence[Text], out: Console | None = None) -> None:
    """Put a block of rail on the terminal and leave it there."""
    Surface(out).draw(lines)


# Components -----------------------------------------------------------------


def choose[ValueT](
    question: str,
    options: Sequence[Option[ValueT]],
    *,
    out: Console | None = None,
) -> Option[ValueT] | None:
    """Ask, and return the option picked, or ``None`` if nothing was.

    The finished step collapses to two settled lines, so what is left on the
    terminal is a record of the choice.
    """
    surface = Surface(out)
    menu = Menu(question, tuple(options))

    with unbuffered() as descriptor, surface.without_cursor():
        surface.draw(menu.render())
        while True:
            try:
                key = read_key(descriptor)
            except KeyboardInterrupt:
                key = Key.ABORT
            if key in (Key.QUIT, Key.ABORT):
                surface.redraw([rail.step("Nothing chosen", tone=rail.Tone.QUIET)])
                return None
            if key is Key.ENTER:
                picked = menu.picked
                surface.redraw(
                    [
                        rail.step(question, tone=rail.Tone.DONE, marker=rail.SETTLED),
                        rail.body(picked.label),
                    ]
                )
                return picked
            menu = menu.press(key)
            surface.redraw(menu.render())


def type_the_word(word: str, question: str, *, out: Console | None = None) -> bool:
    """Require a word to be typed out, not a key to be tapped.

    The comparison ignores case and surrounding space.
    """
    surface = Surface(out)
    surface.draw([rail.step(question, tone=rail.Tone.GRAVE, marker=rail.ALERT)])
    surface.draw([rail.body()])

    surface.draw_open(rail.body("› "))
    try:
        typed = sys.stdin.readline()
    except (KeyboardInterrupt, EOFError):
        typed = ""
    surface.draw([rail.tail()])
    return typed.strip().casefold() == word.casefold()


def abandon(message: str, out: Console | None = None) -> None:
    """Close the rail off without having done anything."""
    write([rail.body(), rail.tail(message)], out)
