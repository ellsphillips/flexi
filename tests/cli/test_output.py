"""Stream setup: encoding tolerance, ANSI escapes, and colour.

The click-drawn commands and the Rich-drawn prompts must reach the same answer
on all three.
"""

from __future__ import annotations

import io
import sys

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


def test_piped_stream_replaces_unencodable_glyphs() -> None:
    """Every delta carries U+2212, which strict cp1252 cannot encode."""
    stream = _Stream(tty=False)
    with pytest.MonkeyPatch.context() as patched:
        _both(patched, stream)
        output.tolerant()

    stream.write("balance −4:14")
    stream.flush()

    assert stream.errors == "replace"
    assert b"balance ?4:14" in stream.bytes.getvalue()


def test_terminal_is_left_strict() -> None:
    stream = _Stream(tty=True)
    with pytest.MonkeyPatch.context() as patched:
        _both(patched, stream)
        output.tolerant()

    assert stream.errors == "strict"


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
