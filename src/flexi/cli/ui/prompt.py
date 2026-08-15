"""The one module here that owns a terminal.

Everything else in ``flexi.cli.ui`` is a pure function of its arguments. This is
where the raw mode, the byte reads and the cursor arithmetic live, kept in one
place so the rest can be tested by pressing keys into a value.

Two decisions worth stating, because both are load-bearing.

**It draws to stderr.** A prompt is not the program's output. Writing it to
stdout means ``flexi init > setup.log`` sends the warning and the question into
the file while the person sits in front of a blank terminal being waited on --
and since the check for "is somebody there" reads stderr, the check and the
writes have to be looking at the same stream or the guard is decorative.

**It never wraps.** Rewinding over what was drawn is line arithmetic, so a line
long enough to wrap would make the redraw eat the line above it. Cropping is not
a nicety here; it is what keeps the redraw correct on an 80-column terminal.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import Final

from rich.console import Console
from rich.text import Text

from flexi.cli.ui import rail
from flexi.cli.ui.keys import Key, decode, incomplete
from flexi.cli.ui.menu import Menu, Option

ESCAPE_WAIT: Final = 0.05
"""Seconds to wait for the rest of an escape sequence.

Long enough to cross a slow link, short enough that pressing escape on its own
does not feel like it stuck."""


def console() -> Console:
    """A console pointed at stderr, with no colour guessing."""
    return Console(stderr=True, highlight=False, markup=False, emoji=False)


def interactive() -> bool:
    """A real terminal to ask at, and a real person to answer.

    Both ends are checked. ``yes | flexi init`` has a terminal to draw on but
    nobody reading it, and ``flexi init > log`` has somebody reading but nothing
    to draw on.
    """
    return sys.stdin.isatty() and sys.stderr.isatty()


# -- reading -----------------------------------------------------------------


@contextmanager
def _unbuffered() -> Iterator[int]:
    """The terminal delivering keys as they are struck.

    ``cbreak`` rather than ``raw``: it leaves signal handling on, so ctrl-c
    still interrupts even if the loop inside this block has gone wrong. Restored
    on the way out however the block is left.

    Windows arrives here already in that state. ``msvcrt.getwch`` reads a
    character straight off the console, unbuffered and unechoed, so there is no
    mode to set and nothing to hand back.
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


def _more_coming(descriptor: int) -> bool:
    import select

    ready, _, _ = select.select([descriptor], [], [], ESCAPE_WAIT)
    return bool(ready)


def _read_posix(descriptor: int) -> Key:
    sequence = os.read(descriptor, 1).decode("utf-8", errors="ignore")
    while incomplete(sequence) and _more_coming(descriptor):
        sequence += os.read(descriptor, 1).decode("utf-8", errors="ignore")
    return decode(sequence)


WINDOWS_PREFIXES: Final = ("\x00", "\xe0")
"""What the Windows console sends ahead of a scan code, rather than an escape."""

WINDOWS_SCANCODES: Final[dict[str, Key]] = {"H": Key.UP, "P": Key.DOWN}
"""The scan codes for the two keys a menu moves on."""


def _read_windows(getwch: Callable[[], str]) -> Key:
    """One key press, as the Windows console delivers it.

    An arrow comes as two reads -- a prefix saying a scan code follows, then the
    code -- rather than as an escape sequence, so there is nothing to wait for
    and no ambiguity between escape and the start of an arrow. Everything else
    arrives whole and is named by the same table POSIX uses.

    The character source is a parameter, and that is what makes this testable
    from a suite running anywhere. On POSIX the risk lives in the terminal mode,
    which is why the reader there is given a real pty; here there is no mode,
    only this two-step protocol, and a function returning characters exercises
    it exactly as ``msvcrt`` would.
    """
    first = getwch()
    if first in WINDOWS_PREFIXES:
        return WINDOWS_SCANCODES.get(getwch(), Key.UNKNOWN)
    return decode(first)


def read_key(descriptor: int) -> Key:
    """One key press, as this platform delivers it."""
    if sys.platform == "win32":  # pragma: no cover - POSIX takes the branch below
        import msvcrt

        return _read_windows(msvcrt.getwch)
    return _read_posix(descriptor)


# -- drawing -----------------------------------------------------------------


class Surface:
    """Lines drawn to the terminal, and the means to take them back.

    Holds the count of what it last drew so it can rewind exactly that far. The
    no-wrap rule in the module docstring is what makes the count trustworthy.
    """

    def __init__(self, out: Console | None = None) -> None:
        self._console = out or console()
        self._drawn = 0

    def draw(self, lines: Sequence[Text]) -> None:
        for line in lines:
            self._console.print(line, no_wrap=True, overflow="crop")
        self._drawn = len(lines)

    def rewind(self) -> None:
        """Put the cursor back where the last draw started, and clear."""
        if self._drawn:
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


# -- components --------------------------------------------------------------


def choose(
    question: str,
    options: Sequence[Option],
    *,
    out: Console | None = None,
) -> Option | None:
    """Ask, and return what was picked -- or ``None`` if it was not.

    The finished step collapses to two settled lines, so a transcript of the
    session reads as a record of what was chosen rather than the wreckage of a
    menu that has been arrowed through.
    """
    surface = Surface(out)
    menu = Menu(question, tuple(options))

    with _unbuffered() as descriptor, surface.without_cursor():
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

    A keystroke can be muscle memory. Making somebody spell the thing out is the
    difference between agreeing and merely continuing, and it is the last gate
    before Flexi deletes anything.
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
