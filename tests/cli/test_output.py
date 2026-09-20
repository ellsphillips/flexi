"""Stream setup: encoding tolerance, ANSI escapes, and colour.

The click-drawn commands and the Rich-drawn prompts must reach the same answer
on all three.
"""

from __future__ import annotations

import io
import sys
from unittest.mock import Mock

import click
import pytest
from click.testing import CliRunner

from flexi.__main__ import cli
from flexi.cli import output


class _Stream(io.TextIOWrapper):
    """A cp1252 text stream that answers ``isatty`` as told."""

    def __init__(self, *, tty: bool) -> None:
        self.bytes = io.BytesIO()
        self._tty = tty
        super().__init__(self.bytes, encoding="cp1252", errors="strict")

    def isatty(self) -> bool:
        return self._tty


def _both(monkeypatch: pytest.MonkeyPatch, stream: object) -> None:
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", stream)


@pytest.mark.parametrize("requested_encoding", [None, ""])
def test_piped_stream_preserves_deficits_and_unicode_notes(
    monkeypatch: pytest.MonkeyPatch, requested_encoding: str | None
) -> None:
    """A locale default must not erase a deficit's sign or a user's note."""
    if requested_encoding is None:
        monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    else:
        monkeypatch.setenv("PYTHONIOENCODING", requested_encoding)
    stdout, stderr = _Stream(tty=False), _Stream(tty=False)
    message = "balance −4:14; note: café 日本語"
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(sys, "stdout", stdout)
        patched.setattr(sys, "stderr", stderr)
        output.tolerant()
        click.echo(message)
        click.echo(message, err=True)

    for stream in (stdout, stderr):
        assert stream.encoding == "utf-8"
        assert stream.bytes.getvalue().decode("utf-8") == message + "\n"


def test_piped_stream_respects_explicit_encoding_with_tolerant_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252:strict")
    stream = _Stream(tty=False)
    with pytest.MonkeyPatch.context() as patched:
        _both(patched, stream)
        output.tolerant()

    stream.write("balance −4:14; note: café 日本語")
    stream.flush()

    assert stream.encoding == "cp1252"
    assert stream.errors == "replace"
    assert stream.bytes.getvalue() == b"balance ?4:14; note: caf\xe9 ???"


def test_terminal_is_left_strict() -> None:
    stream = _Stream(tty=True)
    with pytest.MonkeyPatch.context() as patched:
        _both(patched, stream)
        output.tolerant()

    assert stream.errors == "strict"
    assert stream.encoding == "cp1252"


def test_stream_without_reconfigure_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not every stdout is a ``TextIOWrapper``; some are a test harness."""
    plain = io.StringIO()
    _both(monkeypatch, plain)

    output.tolerant()

    assert plain.getvalue() == ""


def test_posix_terminal_needs_no_ansi_flag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Console mode is a Windows API; elsewhere there is nothing to enable."""
    output.enable_ansi()

    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("console", [True, False], ids=["console", "redirected"])
def test_windows_console_uses_pointer_sized_handles(
    monkeypatch: pytest.MonkeyPatch, console: bool
) -> None:
    import ctypes
    from ctypes import wintypes

    handle = 0x123456789
    original_mode = 0x0001

    def read_mode(received: int, pointer: ctypes.c_void_p) -> bool:
        assert received == handle
        ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD))[0] = original_mode
        return console

    kernel = Mock()
    kernel.GetStdHandle.return_value = handle
    kernel.GetConsoleMode.side_effect = read_mode
    monkeypatch.setattr(ctypes, "WinDLL", Mock(return_value=kernel), raising=False)
    monkeypatch.setattr(sys, "platform", "win32")

    output.enable_ansi()

    assert kernel.GetStdHandle.restype is wintypes.HANDLE
    assert kernel.GetStdHandle.argtypes == [wintypes.DWORD]
    assert kernel.GetConsoleMode.argtypes == [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    assert kernel.GetConsoleMode.restype is wintypes.BOOL
    assert kernel.SetConsoleMode.argtypes == [wintypes.HANDLE, wintypes.DWORD]
    assert kernel.SetConsoleMode.restype is wintypes.BOOL
    assert kernel.GetStdHandle.call_count == 2
    if console:
        assert kernel.SetConsoleMode.call_count == 2
        kernel.SetConsoleMode.assert_called_with(handle, original_mode | 0x0004)
    else:
        kernel.SetConsoleMode.assert_not_called()


@pytest.mark.parametrize(
    ("variables", "expected"),
    [
        pytest.param({}, False, id="a full colour terminal"),
        pytest.param({"NO_COLOR": "1"}, True, id="no-color.org"),
        pytest.param({"NO_COLOR": ""}, False, id="empty means unset"),
        pytest.param({"TERM": "dumb"}, True, id="a terminal that says so"),
        pytest.param({"TERM": "DUMB"}, True, id="however it is spelled"),
        pytest.param({"TERM": "xterm-256color"}, False, id="an ordinary terminal"),
    ],
)
def test_monochrome_reads_no_color_and_term(
    variables: dict[str, str], expected: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    for name, value in variables.items():
        monkeypatch.setenv(name, value)

    assert output.monochrome() is expected


def test_preparing_turns_click_monochrome_when_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    ctx = click.Context(click.Command("anything"))

    output.prepare(ctx)

    assert ctx.color is False


def test_preparing_leaves_colour_to_click_otherwise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    ctx = click.Context(click.Command("anything"))

    output.prepare(ctx)

    assert ctx.color is None


@pytest.mark.parametrize(
    "variable", [("NO_COLOR", "1"), ("TERM", "dumb")], ids=["NO_COLOR", "TERM"]
)
def test_commands_go_monochrome_with_prompts(
    variable: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Click reads neither setting, so the group callback hands it the answer."""
    monkeypatch.setenv(*variable)

    result = CliRunner().invoke(cli, ["balance", "show"], color=True)

    assert result.exit_code == 1
    assert "not set up" in result.output
    assert "\x1b[" not in result.output


def test_colour_survives_a_terminal_that_wants_it() -> None:
    """Guards the test above from passing on a command that never colours."""
    result = CliRunner().invoke(cli, ["balance", "show"], color=True)

    assert "\x1b[" in result.output
