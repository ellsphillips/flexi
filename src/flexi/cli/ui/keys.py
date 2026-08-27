"""What the terminal sends, given a name.

A terminal delivers bytes, not key presses. An arrow arrives as three of them,
and which three depends on whether the terminal is in application cursor mode.
Naming them is a pure function of the sequence, so every key Flexi reads can be
tested with nothing attached -- which matters, because the module that does hold
a terminal has to put it in raw mode before it can read a single byte, and raw
mode is not something to go near in a test suite.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum, unique
from types import MappingProxyType
from typing import Final

ESCAPE: Final = "\x1b"


@unique
class Key(Enum):
    """A key press, once its escape sequence has been read to the end."""

    UP = "up"
    DOWN = "down"
    ENTER = "enter"
    QUIT = "quit"
    ABORT = "abort"
    UNKNOWN = "unknown"


# Both cursor modes are listed. A terminal left in application mode -- which is
# where Textual leaves it, so where `flexi init` finds it after the setup form
# has been open -- sends ESC O A for up, where the default mode sends ESC [ A.
# Reading only one makes the arrows work everywhere except straight after the
# application, which is the worst half to get.
KEYS: Final[Mapping[str, Key]] = MappingProxyType(
    {
        "\x1b[A": Key.UP,
        "\x1bOA": Key.UP,
        "\x1b[B": Key.DOWN,
        "\x1bOB": Key.DOWN,
        "k": Key.UP,
        "j": Key.DOWN,
        "\r": Key.ENTER,
        "\n": Key.ENTER,
        " ": Key.ENTER,
        ESCAPE: Key.QUIT,
        "q": Key.QUIT,
        "\x03": Key.ABORT,
        "\x04": Key.ABORT,
    }
)

PREFIXES: Final = (ESCAPE, ESCAPE + "[", ESCAPE + "O")


def decode(sequence: str) -> Key:
    r"""Name the key a sequence stands for.

    >>> decode("\x1b[B") is Key.DOWN
    True
    >>> decode("\x03") is Key.ABORT
    True
    """
    return KEYS.get(sequence, Key.UNKNOWN)


def incomplete(sequence: str) -> bool:
    r"""Whether more bytes are needed before this can be named.

    A lone escape and the first byte of an arrow key are the same byte. Whether
    anything follows is the only thing that separates them, so the reader has to
    know when it is worth waiting to find out.

    >>> incomplete("\x1b")
    True
    >>> incomplete("\x1b[A")
    False
    """
    return sequence in PREFIXES
