"""The prompt reader against a real terminal, which means a POSIX one.

`pty` and `termios` do not exist on Windows, so these tests are skipped there.
Nothing equivalent is lost: `msvcrt.getwch` is unbuffered and unechoed by
construction, and `test_ui.py` covers the scan-code protocol on every platform.
"""

from __future__ import annotations

import sys

import pytest

if sys.platform == "win32":  # pragma: no cover - the module is skipped there
    pytest.skip("pty and termios are POSIX", allow_module_level=True)

import os
import pty
import termios
from collections.abc import Iterator

from flexi.cli.ui import prompt
from flexi.cli.ui.keys import Key


class _Descriptor:
    """Just enough of a stdin for `unbuffered`, which asks it for a number."""

    def __init__(self, descriptor: int) -> None:
        self._descriptor = descriptor

    def fileno(self) -> int:
        return self._descriptor


@pytest.fixture
def pty_pair(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[int, int]]:
    """A real terminal, with `sys.stdin` pointed at the far end of it.

    A pty is the smallest thing with a line discipline to put into cbreak.
    """
    controller, terminal = pty.openpty()
    monkeypatch.setattr(sys, "stdin", _Descriptor(terminal))
    try:
        yield controller, terminal
    finally:
        os.close(controller)
        os.close(terminal)


def test_arrow_key_reads_as_one_press(
    pty_pair: tuple[int, int],
) -> None:
    controller, _ = pty_pair

    with prompt.unbuffered() as descriptor:
        os.write(controller, b"\x1b[B")

        assert prompt.read_key(descriptor) is Key.DOWN


def test_lone_escape_is_answered(
    pty_pair: tuple[int, int],
) -> None:
    """A lone escape and the first byte of an arrow are the same byte.

    The reader waits a moment for the rest of a sequence and then gives up.
    """
    controller, _ = pty_pair

    with prompt.unbuffered() as descriptor:
        os.write(controller, b"\x1b")

        assert prompt.read_key(descriptor) is Key.QUIT


def test_undecodable_byte_reads_as_unknown(
    pty_pair: tuple[int, int],
) -> None:
    """Bytes are read one at a time.

    The first half of a multi-byte character decodes to nothing, and nothing is
    an unknown key.
    """
    controller, _ = pty_pair

    with prompt.unbuffered() as descriptor:
        os.write(controller, "é".encode())

        assert prompt.read_key(descriptor) is Key.UNKNOWN


def test_cbreak_is_set_inside_the_block(
    pty_pair: tuple[int, int],
) -> None:
    """A terminal in its default mode hands nothing over until enter is pressed."""
    _, _terminal = pty_pair

    with prompt.unbuffered() as descriptor:
        mode = termios.tcgetattr(descriptor)

    assert not mode[3] & termios.ICANON, "still waiting for a whole line"
    assert not mode[3] & termios.ECHO, "the keystrokes would print themselves"


def test_terminal_mode_is_restored_on_error(
    pty_pair: tuple[int, int],
) -> None:
    """Cbreak is a change to the shell's terminal, not to Flexi's.

    Only the three local flags the mode is made of are compared: the driver sets
    `PENDIN` on the way through a mode change, so the whole attribute list comes
    back equal in substance and unequal in value.
    """
    _, terminal = pty_pair
    mode = termios.ICANON | termios.ECHO | termios.ISIG
    before = termios.tcgetattr(terminal)[3] & mode

    msg = "the loop inside went wrong"

    with pytest.raises(RuntimeError, match=msg), prompt.unbuffered():
        raise RuntimeError(msg)

    assert termios.tcgetattr(terminal)[3] & mode == before
