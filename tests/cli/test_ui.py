"""Flexi's own prompts, driven without a terminal.

`keys`, `rail` and `menu` are pure functions of a value, so a menu is pressed by
handing it a key and reading what comes back. `prompt` draws on a Rich console
and reads from a file descriptor, so a string stands in for a screen and a pty
for a keyboard.
"""

from __future__ import annotations

import io
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager

import pytest
from rich.console import Console
from rich.text import Text

from flexi.cli.ui import prompt, rail
from flexi.cli.ui.keys import Key, decode, incomplete
from flexi.cli.ui.menu import HINT, Menu, Option
from flexi.theme import CURSOR, TAIL


def styles_on(line: Text, needle: str) -> list[str]:
    """Every non-empty style covering the first character of `needle`."""
    start = line.plain.index(needle)
    return [
        str(span.style)
        for span in line.spans
        if span.start <= start < span.end and str(span.style)
    ]


# ---- keys ----


@pytest.mark.parametrize(
    ("sequence", "expected"),
    [
        ("\x1b[A", Key.UP),
        ("\x1b[B", Key.DOWN),
        ("\x1bOA", Key.UP),
        ("\x1bOB", Key.DOWN),
        ("k", Key.UP),
        ("j", Key.DOWN),
        ("\r", Key.ENTER),
        ("\n", Key.ENTER),
        (" ", Key.ENTER),
        ("\x1b", Key.QUIT),
        ("q", Key.QUIT),
        ("\x03", Key.ABORT),
        ("\x04", Key.ABORT),
        ("z", Key.UNKNOWN),
    ],
)
def test_terminal_vocabulary(sequence: str, expected: Key) -> None:
    assert decode(sequence) is expected


def test_application_cursor_mode_is_read_too() -> None:
    """Textual leaves the terminal in application mode, and `flexi init` follows."""
    assert decode("\x1bOA") is decode("\x1b[A")


def test_escape_is_incomplete_until_the_sequence_ends() -> None:
    assert incomplete("\x1b")
    assert incomplete("\x1b[")
    assert not incomplete("\x1b[A")
    assert not incomplete("j")


# ---- the rail ----


def test_live_rail_is_heavy_and_settled_is_hairline() -> None:
    assert rail.HEAVY in rail.body(tone=rail.Tone.LIVE).plain
    assert rail.HAIRLINE in rail.body().plain


def test_rail_colours_itself_and_nothing_after_it() -> None:
    """`Text(s, style=...)` styles the whole object, including later appends."""
    assert styles_on(rail.body("plain words"), "plain words") == []
    assert styles_on(rail.body("plain words"), rail.HAIRLINE) != []


def test_destructive_row_is_red_when_unpicked() -> None:
    resting = rail.option("Start again", "erase everything", picked=False, grave=True)
    assert styles_on(resting, "Start again") == [rail.Tone.GRAVE.style]


def test_ordinary_row_is_default_ink_until_picked() -> None:
    assert (
        styles_on(rail.option("Open Flexi", "hint", picked=False), "Open Flexi") == []
    )
    picked = rail.option("Open Flexi", "hint", picked=True)
    assert styles_on(picked, "Open Flexi") == [f"bold {rail.Tone.LIVE.style}"]


def test_cursor_glyph_marks_the_selection() -> None:
    """Colour is not the only encoding: the picked row carries a glyph too."""
    assert CURSOR in rail.option("a", "", picked=True).plain
    assert CURSOR not in rail.option("a", "", picked=False).plain


def test_hints_line_up_in_a_column() -> None:
    short = rail.option("Open", "hint", picked=False)
    long = rail.option("Change settings", "hint", picked=False)
    assert short.plain.index("hint") == long.plain.index("hint")


# ---- the menu ----


def a_menu() -> Menu[str]:
    return Menu(
        "What would you like to do?",
        (
            Option("open", "Open Flexi"),
            Option("settings", "Change settings"),
            Option("reset", "Start again", grave=True),
        ),
    )


def test_menu_starts_on_the_safe_option() -> None:
    assert a_menu().picked.value == "open"
    assert not a_menu().picked.grave


def test_arrows_move_the_cursor() -> None:
    assert a_menu().press(Key.DOWN).picked.value == "settings"
    assert a_menu().press(Key.DOWN).press(Key.DOWN).picked.value == "reset"


def test_menu_wraps_at_both_ends() -> None:
    assert a_menu().press(Key.UP).picked.value == "reset"
    walked = a_menu().press(Key.DOWN).press(Key.DOWN).press(Key.DOWN)
    assert walked.picked.value == "open"


def test_unknown_key_changes_nothing() -> None:
    menu = a_menu()
    assert menu.press(Key.UNKNOWN) == menu


def test_pressing_a_key_returns_a_new_menu() -> None:
    menu = a_menu()
    assert menu.press(Key.DOWN) is not menu
    assert menu.cursor == 0


def test_every_option_is_drawn() -> None:
    drawn = "\n".join(line.plain for line in a_menu().render())
    for label in ("Open Flexi", "Change settings", "Start again"):
        assert label in drawn
    assert "↑↓ move" in drawn


def test_empty_menu_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least one option"):
        Menu("nothing", ())


# ---- the rest of the rail ----


def test_wordmark_names_flexi_in_the_accent() -> None:
    mark = rail.wordmark()

    assert "flexi" in mark.plain
    assert styles_on(mark, "flexi") == [f"bold {rail.Tone.LIVE.style}"]


def test_counts_are_right_aligned_in_a_column() -> None:
    one = rail.measure(7, "work sessions")
    many = rail.measure(1204, "clock events")

    assert one.plain.index("7") == many.plain.index("1204") + len("1204") - 1
    assert one.plain.index("work sessions") == many.plain.index("clock events")


def test_tail_closes_the_rail_with_or_without_hint() -> None:
    assert rail.tail().plain.strip() == TAIL
    assert rail.tail("esc cancel").plain.endswith("esc cancel")


# ---- the terminal ----


def paper(width: int = 60, *, terminal: bool = False) -> tuple[Console, io.StringIO]:
    """A console that writes to a string, so a prompt can be read back.

    `force_terminal` is on only for the cursor hide and show sequences Rich
    withholds from a plain file; off, the text carries no colour.
    Virtual-terminal behaviour is explicit; the legacy-console test covers
    consoles that cannot interpret cursor escapes.
    """
    stream = io.StringIO()
    return Console(
        file=stream, width=width, force_terminal=terminal, legacy_windows=False
    ), stream


def visible(stream: io.StringIO) -> str:
    """The last frame: everything the stream holds after the final rewind.

    The stream keeps every frame of a menu being arrowed through, a terminal
    only the last.
    """
    return stream.getvalue().rsplit("\x1b[0J", 1)[-1]


class _Tty:
    """A stream that is, or is not, a terminal."""

    def __init__(self, *, tty: bool) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


@pytest.mark.parametrize(
    ("stdin_is_a_tty", "stderr_is_a_tty", "expected"),
    [
        pytest.param(True, True, True, id="a person at a terminal"),
        pytest.param(False, True, False, id="yes | flexi init"),
        pytest.param(True, False, False, id="flexi init > log"),
    ],
)
def test_interactive_needs_a_tty_on_both_ends(
    monkeypatch: pytest.MonkeyPatch,
    stdin_is_a_tty: bool,
    stderr_is_a_tty: bool,
    expected: bool,
) -> None:
    """Either end alone is a hang: a pipe on stdin, a redirect on stderr."""
    monkeypatch.setattr(sys, "stdin", _Tty(tty=stdin_is_a_tty))
    monkeypatch.setattr(sys, "stderr", _Tty(tty=stderr_is_a_tty))

    assert prompt.interactive() is expected


def test_prompts_are_written_to_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`flexi init > setup.log` must not send the question into the file.

    `interactive` reads stderr, so the writes go to the stream the guard checks.
    """
    prompt.console().print("Already set up")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Already set up" in captured.err


def test_brackets_are_shown_not_read_as_markup(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Rich markup is off: `reset` is both the word to type and a Rich style."""
    prompt.console().print("Type [reset] to continue")

    assert "Type [reset] to continue" in capsys.readouterr().err


def test_overlong_line_is_cropped() -> None:
    """A wrapped line would make the next redraw eat the line above it."""
    console, stream = paper(width=20)

    prompt.Surface(console).draw([Text("a database path that is far too long")])

    drawn = stream.getvalue().splitlines()
    assert len(drawn) == 1
    assert len(drawn[0]) <= 20


def test_redraw_takes_back_the_lines_it_drew() -> None:
    console, stream = paper()
    surface = prompt.Surface(console)

    surface.draw([Text("one"), Text("two"), Text("three")])
    surface.redraw([Text("only this")])

    assert "\x1b[3F\x1b[0J" in stream.getvalue()
    assert visible(stream).strip() == "only this"


def test_legacy_console_is_sent_no_escapes() -> None:
    """Legacy conhost prints an escape sequence instead of acting on it.

    `rewind` writes to the console's file, past Rich's legacy renderer, so
    `Surface` has to make the choice itself.
    """
    stream = io.StringIO()
    console = Console(file=stream, width=60, legacy_windows=True)
    surface = prompt.Surface(console)

    surface.draw([Text("one"), Text("two")])
    surface.redraw([Text("only this")])

    assert "\x1b" not in stream.getvalue()
    assert stream.getvalue().splitlines()[-1].strip() == "only this"


def test_rewind_before_any_draw_writes_nothing() -> None:
    """The line above Flexi's first output belongs to the shell."""
    console, stream = paper()

    prompt.Surface(console).rewind()

    assert stream.getvalue() == ""


def test_draw_open_keeps_the_cursor_on_the_line() -> None:
    console, stream = paper()

    prompt.Surface(console).draw_open(Text("› "))

    assert not stream.getvalue().endswith("\n")


def test_cursor_is_restored_after_an_exception() -> None:
    """A menu hides the cursor, and a hidden cursor outlives the process."""
    console, stream = paper(terminal=True)

    surface = prompt.Surface(console)
    msg = "the terminal went away"

    with pytest.raises(RuntimeError, match=msg), surface.without_cursor():
        raise RuntimeError(msg)

    assert stream.getvalue().endswith("\x1b[?25h")


def test_abandoning_closes_the_rail_and_says_why() -> None:
    """Every path out of `flexi init` ends with the rail drawn to its end."""
    console, stream = paper()

    prompt.abandon("Nothing was changed", console)

    assert TAIL in visible(stream)
    assert "Nothing was changed" in visible(stream)


# ---- reading keys off a Windows console ----
#
# The POSIX reader needs a real pty and is covered in `test_terminal.py`.
# Windows has no mode, only the two-step scan-code protocol below, so a function
# returning characters stands in for `msvcrt`.


def typing(*characters: str) -> Callable[[], str]:
    """A Windows keyboard, one `getwch` call at a time."""
    return iter(characters).__next__


@pytest.mark.parametrize(
    ("characters", "expected"),
    [
        pytest.param(("\x00", "H"), Key.UP, id="up, as the console sends it"),
        pytest.param(("\xe0", "P"), Key.DOWN, id="down, from the extended pad"),
        pytest.param(("j",), Key.DOWN, id="a letter arrives whole"),
        pytest.param(("\r",), Key.ENTER, id="enter"),
        pytest.param(("\x1b",), Key.QUIT, id="escape, with nothing to wait for"),
        pytest.param(("\x03",), Key.ABORT, id="ctrl-c, which getwch hands over"),
    ],
)
def test_windows_console_vocabulary(characters: tuple[str, ...], expected: Key) -> None:
    r"""`\x00` and `\xe0` both mean a scan code follows."""
    assert prompt.read_windows(typing(*characters)) is expected


def test_unused_scan_code_is_read_whole() -> None:
    """F1 is two reads, and swallowing only the first desynchronises the loop."""
    keyboard = typing("\x00", ";", "j")

    assert prompt.read_windows(keyboard) is Key.UNKNOWN
    assert prompt.read_windows(keyboard) is Key.DOWN


def test_escape_on_windows_is_answered_at_once() -> None:
    """No arrow begins with escape on a Windows console, so no wait is needed."""
    keyboard = typing("\x1b", "\x00", "H")

    assert prompt.read_windows(keyboard) is Key.QUIT
    assert prompt.read_windows(keyboard) is Key.UP


# ---- choosing ----


@contextmanager
def _no_terminal() -> Iterator[int]:
    """Yield a descriptor without putting a terminal into cbreak."""
    yield -1


def options() -> Sequence[Option[str]]:
    return (
        Option("open", "Open Flexi", "your records, as they are"),
        Option("settings", "Change settings", "leave year, working days, region"),
        Option("reset", "Start again", "erase 12 records", grave=True),
    )


@pytest.fixture
def pressing(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Press the keys given, in order."""
    monkeypatch.setattr(prompt, "unbuffered", _no_terminal)

    def press(*keys: Key) -> None:
        presses = iter(keys)

        def read(_descriptor: int) -> Key:
            return next(presses)

        monkeypatch.setattr(prompt, "read_key", read)

    return press


def test_enter_returns_the_row_arrowed_to(
    pressing: Callable[..., None],
) -> None:
    console, _ = paper()
    pressing(Key.DOWN, Key.ENTER)

    picked = prompt.choose("What would you like to do?", options(), out=console)

    assert picked is not None
    assert picked.value == "settings"


def test_answered_step_collapses_to_question_and_answer(
    pressing: Callable[..., None],
) -> None:
    console, stream = paper()
    pressing(Key.DOWN, Key.DOWN, Key.ENTER)

    prompt.choose("What would you like to do?", options(), out=console)

    left = visible(stream)
    assert "What would you like to do?" in left
    assert "Start again" in left
    assert "Open Flexi" not in left, "the rows not taken are gone"
    assert HINT not in left, "so are the keys that no longer do anything"


@pytest.mark.parametrize(
    "key",
    [pytest.param(Key.QUIT, id="escape"), pytest.param(Key.ABORT, id="ctrl-c")],
)
def test_backing_out_chooses_nothing_and_says_so(
    pressing: Callable[..., None], key: Key
) -> None:
    """`choose` returns `None`, which the caller reads as "leave it alone"."""
    console, stream = paper()
    pressing(key)

    assert prompt.choose("What would you like to do?", options(), out=console) is None
    assert "Nothing chosen" in visible(stream)
    assert "Start again" not in visible(stream)


def test_keyboard_interrupt_is_a_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cbreak leaves signal handling on, so ctrl-c arrives as an exception."""
    console, stream = paper()

    def interrupted(_descriptor: int) -> Key:
        raise KeyboardInterrupt

    monkeypatch.setattr(prompt, "unbuffered", _no_terminal)
    monkeypatch.setattr(prompt, "read_key", interrupted)

    assert prompt.choose("What would you like to do?", options(), out=console) is None
    assert "Nothing chosen" in visible(stream)


def test_unknown_key_leaves_the_cursor_alone(
    pressing: Callable[..., None],
) -> None:
    console, _ = paper()
    pressing(Key.UNKNOWN, Key.ENTER)

    picked = prompt.choose("What would you like to do?", options(), out=console)

    assert picked is not None
    assert picked.value == "open"


# ---- typing the word ----


@pytest.mark.parametrize(
    "typed",
    [
        pytest.param("reset\n", id="as asked"),
        pytest.param("  reset  \n", id="with the spaces a paste brings"),
        pytest.param("RESET\n", id="shouted"),
    ],
)
def test_spelling_the_word_opens_the_gate(
    monkeypatch: pytest.MonkeyPatch, typed: str
) -> None:
    console, _ = paper()
    monkeypatch.setattr(sys, "stdin", io.StringIO(typed))

    assert prompt.type_the_word("reset", "Type 'reset' to continue", out=console)


@pytest.mark.parametrize(
    "typed",
    [
        pytest.param("y\n", id="the answer to a yes/no question"),
        pytest.param("resett\n", id="a near miss"),
        pytest.param("\n", id="enter, to get past it"),
        pytest.param("", id="nothing at all"),
    ],
)
def test_anything_else_typed_is_a_refusal(
    monkeypatch: pytest.MonkeyPatch, typed: str
) -> None:
    """The empty line counts: it is what `yes '' | flexi init` sends."""
    console, _ = paper()
    monkeypatch.setattr(sys, "stdin", io.StringIO(typed))

    assert not prompt.type_the_word("reset", "Type 'reset' to continue", out=console)


class _Interrupted:
    """A stdin that goes away mid-question, as ctrl-c and ctrl-d both do."""

    def __init__(self, raises: type[BaseException]) -> None:
        self._raises = raises

    def readline(self) -> str:
        raise self._raises


@pytest.mark.parametrize(
    "interruption",
    [pytest.param(KeyboardInterrupt, id="ctrl-c"), pytest.param(EOFError, id="ctrl-d")],
)
def test_abandoned_confirmation_deletes_nothing(
    monkeypatch: pytest.MonkeyPatch, interruption: type[BaseException]
) -> None:
    """Getting out of the last question is a refusal, not a traceback."""
    console, _ = paper()
    monkeypatch.setattr(sys, "stdin", _Interrupted(interruption))

    assert not prompt.type_the_word("reset", "Type 'reset' to continue", out=console)


def test_confirmation_closes_the_rail_either_way(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The answer stays on screen above a closed rail, in the scrollback."""
    console, stream = paper()
    monkeypatch.setattr(sys, "stdin", io.StringIO("no\n"))

    prompt.type_the_word("reset", "Type 'reset' to continue", out=console)

    assert stream.getvalue().rstrip().endswith(rail.tail().plain.rstrip())
