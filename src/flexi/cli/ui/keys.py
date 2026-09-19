"""Key names for the byte sequences a terminal sends.

An arrow arrives as three bytes, and which three depends on the cursor mode.
Naming a sequence is a pure function of it; holding a terminal, which needs
raw mode, happens in :mod:`flexi.cli.ui.prompt`.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum, unique
from types import MappingProxyType
from typing import Final

__all__ = ("ESCAPE", "KEYS", "PREFIXES", "Key", "decode", "incomplete")

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


# Both cursor modes are listed. Application mode, which is where Textual leaves
# the terminal, sends ESC O A for up; the default mode sends ESC [ A.
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
    r"""Report whether more bytes are needed before this can be named.

    A lone escape and the first byte of an arrow key are the same byte, so only
    what follows separates them.

    >>> incomplete("\x1b")
    True
    >>> incomplete("\x1b[A")
    False
    """
    return sequence in PREFIXES
