"""What the two output streams are told before anything is written to them.

Three answers that belong to the terminal rather than to Flexi: whether it
obeys an escape sequence, whether colour was asked for, and whether it can
encode a U+2212. Each was settled nowhere, so the click-drawn commands and the
Rich-drawn prompts disagreed about all three.
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
    """A text stream that is, or is not, somebody's terminal."""

    def __init__(self, *, tty: bool) -> None:
        self.bytes = io.BytesIO()
        self._tty = tty
        super().__init__(self.bytes, encoding="cp1252", errors="strict")

    def isatty(self) -> bool:
        return self._tty


def _both(monkeypatch: pytest.MonkeyPatch, stream: object) -> None:
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", stream)


def test_a_piped_stream_prints_a_replacement_rather_than_dying() -> None:
    """`flexi balance show > balance.txt` on Windows is a cp1252 stream.

    Every delta carries U+2212 and the leave-year line an arrow, so a strict
    stream raises `UnicodeEncodeError` from inside `click.echo` and the command
    dies after doing its work. A lost glyph is smaller than a lost line.
    """
    stream = _Stream(tty=False)
    with pytest.MonkeyPatch.context() as patched:
        _both(patched, stream)
        output.tolerant()

    stream.write("balance −4:14")
    stream.flush()

    assert stream.errors == "replace"
    assert b"balance ?4:14" in stream.bytes.getvalue()


def test_a_terminal_is_left_exactly_as_it_is() -> None:
    """A console encodes the glyphs, and nothing here should soften it."""
    stream = _Stream(tty=True)
    with pytest.MonkeyPatch.context() as patched:
        _both(patched, stream)
        output.tolerant()

    assert stream.errors == "strict"


def test_a_stream_with_nothing_to_reconfigure_is_passed_over(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not every stdout is a `TextIOWrapper`; some are a test harness."""
    plain = io.StringIO()
    _both(monkeypatch, plain)

    output.tolerant()

    assert plain.getvalue() == ""


def test_a_posix_terminal_needs_no_flag_turned_on(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The console mode is Windows's. Here there is nothing to enable."""
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
def test_the_environment_is_read_the_way_rich_reads_it(
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
def test_the_commands_go_monochrome_with_the_prompts(
    variable: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Click reads neither setting, so the group callback hands it the answer.

    The `flexi init` rail and the application honour both while every
    `click.secho` in the package keeps emitting colour on a terminal.
    """
    monkeypatch.setenv(*variable)

    result = CliRunner().invoke(cli, ["balance", "show"], color=True)

    assert result.exit_code == 1
    assert "not set up" in result.output
    assert "\x1b[" not in result.output


def test_colour_survives_a_terminal_that_wants_it() -> None:
    """Otherwise the test above would pass on a command that never colours."""
    result = CliRunner().invoke(cli, ["balance", "show"], color=True)

    assert "\x1b[" in result.output
