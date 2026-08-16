"""The prompt reader against a real terminal, which means a POSIX one.

Split out of `test_ui.py` because these are the only tests in the suite that
cannot run on Windows: they need `pty` to open a terminal and `termios` to look
at the mode the reader put it in, and neither module exists there.

Nothing is lost on Windows, because there is nothing equivalent to check. The
POSIX reader has to arrange cbreak before it can read a byte, and a mocked
`termios` would agree with whatever the code did to it -- so the mode is
asserted against a driver that really has one. The Windows reader arranges
nothing: `msvcrt.getwch` is unbuffered and unechoed by construction, so all
that is left there is the scan-code protocol, and `test_ui.py` tests that on
every platform by handing the reader a function that returns characters.
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
    """Just enough of a stdin for `_unbuffered`, which asks it for a number."""

    def __init__(self, descriptor: int) -> None:
        self._descriptor = descriptor

    def fileno(self) -> int:
        return self._descriptor


@pytest.fixture
def pty_pair(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[int, int]]:
    """A real terminal, with `sys.stdin` pointed at the far end of it.

    A fake `termios` would agree with whatever the reader did to it, which is
    no test of raw mode at all. A pty is the smallest thing that has a line
    discipline to put into cbreak and a buffer to type into.
    """
    controller, terminal = pty.openpty()
    monkeypatch.setattr(sys, "stdin", _Descriptor(terminal))
    try:
        yield controller, terminal
    finally:
        os.close(controller)
        os.close(terminal)


def test_an_arrow_key_arrives_as_three_bytes_and_is_read_as_one_press(
    pty_pair: tuple[int, int],
) -> None:
    """Three bytes have to become one key, or the arrows do nothing.

    The reader keeps going while the sequence is incomplete and something is
    still coming, which is the only way that happens.
    """
    controller, _ = pty_pair

    with prompt._unbuffered() as descriptor:
        os.write(controller, b"\x1b[B")

        assert prompt.read_key(descriptor) is Key.DOWN


def test_an_escape_on_its_own_is_answered_rather_than_waited_on(
    pty_pair: tuple[int, int],
) -> None:
    """The wait for the rest of a sequence has to end.

    A lone escape and the first byte of an arrow are the same byte, so the
    reader waits a moment to find out which it was. Pressing escape to back out
    of a question and having nothing happen is indistinguishable from a prompt
    that has hung.
    """
    controller, _ = pty_pair

    with prompt._unbuffered() as descriptor:
        os.write(controller, b"\x1b")

        assert prompt.read_key(descriptor) is Key.QUIT


def test_a_key_this_terminal_cannot_name_is_ignored_rather_than_fatal(
    pty_pair: tuple[int, int],
) -> None:
    """Bytes are read one at a time.

    The first half of a multi-byte character decodes to nothing, nothing is an
    unknown key, and an unknown key leaves the menu where it was -- which is a
    great deal better than a traceback over a half-drawn prompt.
    """
    controller, _ = pty_pair

    with prompt._unbuffered() as descriptor:
        os.write(controller, "é".encode())

        assert prompt.read_key(descriptor) is Key.UNKNOWN


def test_the_terminal_stops_waiting_for_a_line_inside_the_block(
    pty_pair: tuple[int, int],
) -> None:
    """A menu answers to a single keystroke.

    A terminal in its default mode hands nothing over until enter is pressed.
    """
    _, terminal = pty_pair

    with prompt._unbuffered() as descriptor:
        mode = termios.tcgetattr(descriptor)

    assert not mode[3] & termios.ICANON, "still waiting for a whole line"
    assert not mode[3] & termios.ECHO, "the keystrokes would print themselves"


def test_the_terminal_is_handed_back_however_the_block_is_left(
    pty_pair: tuple[int, int],
) -> None:
    """Cbreak is a change to the shell's terminal, not to Flexi's.

    Left set, it is a shell that echoes nothing and answers to no line editing
    at all, and nothing on screen says what happened.

    Only the local flags are compared, and only the three the mode is made of:
    the driver sets `PENDIN` on the way through a mode change, so the whole
    attribute list comes back equal in substance and unequal in value.
    """
    _, terminal = pty_pair
    mode = termios.ICANON | termios.ECHO | termios.ISIG
    before = termios.tcgetattr(terminal)[3] & mode

    msg = "the loop inside went wrong"

    with pytest.raises(RuntimeError, match=msg), prompt._unbuffered():
        raise RuntimeError(msg)

    assert termios.tcgetattr(terminal)[3] & mode == before
