"""Flexi's own prompts, driven without a terminal.

The whole point of splitting `keys`, `rail` and `menu` out of `prompt` is that
everything a person sees is a pure function of a value. A menu is pressed by
handing it a key and reading what comes back, so the interaction is tested at
full speed with nothing attached and no sleeping.

What is left in `prompt` is the terminal itself, and it is written to be handed
one: the drawing takes a Rich console, so a string can stand in for a screen,
and the reading takes a file descriptor, so a pty can stand in for a keyboard.
Only two functions genuinely need the pty -- the ones that put the driver into
cbreak and read bytes out of it -- and they get one rather than a mock, because
a mocked `termios` would agree with whatever the code did to it.
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


def styles_on(line: Text, needle: str) -> list[str]:
    """Every non-empty style covering the first character of `needle`."""
    start = line.plain.index(needle)
    return [
        str(span.style)
        for span in line.spans
        if span.start <= start < span.end and str(span.style)
    ]


# -- keys --------------------------------------------------------------------


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
def test_the_terminal_vocabulary(sequence: str, expected: Key) -> None:
    assert decode(sequence) is expected


def test_application_cursor_mode_is_read_too() -> None:
    """Textual leaves the terminal in application mode.

    `flexi init` runs straight after the setup form closes, so reading only the
    default mode would break the arrows in exactly the place they are needed.
    """
    assert decode("\x1bOA") is decode("\x1b[A")


def test_an_escape_alone_is_only_known_once_nothing_follows() -> None:
    assert incomplete("\x1b")
    assert incomplete("\x1b[")
    assert not incomplete("\x1b[A")
    assert not incomplete("j")


# -- the rail ----------------------------------------------------------------


def test_the_live_rail_is_heavy_and_a_settled_one_is_hairline() -> None:
    """The weight is the whole signal.

    That the two glyphs differ is a Literal comparison mypy settles statically,
    so asserting it here would be checking the type checker.
    """
    assert rail.HEAVY in rail.body(tone=rail.Tone.LIVE).plain
    assert rail.HAIRLINE in rail.body().plain


def test_the_rail_colours_itself_and_nothing_after_it() -> None:
    """`Text(s, style=...)` styles the whole object, including later appends.

    Building the rail that way painted every label in the accent, which left the
    weight and the colour both saying "live" on every line of the flow.
    """
    assert styles_on(rail.body("plain words"), "plain words") == []
    assert styles_on(rail.body("plain words"), rail.HAIRLINE) != []


def test_a_destructive_row_is_red_before_you_land_on_it() -> None:
    """Finding out by arrowing onto it is one keystroke too late."""
    resting = rail.option("Start again", "erase everything", picked=False, grave=True)
    assert styles_on(resting, "Start again") == [rail.Tone.GRAVE.style]


def test_an_ordinary_row_is_left_in_default_ink_until_picked() -> None:
    assert (
        styles_on(rail.option("Open Flexi", "hint", picked=False), "Open Flexi") == []
    )
    picked = rail.option("Open Flexi", "hint", picked=True)
    assert styles_on(picked, "Open Flexi") == [f"bold {rail.Tone.LIVE.style}"]


def test_the_cursor_carries_the_selection_as_well_as_the_colour() -> None:
    """Colour alone would leave somebody who cannot see teal with no cursor."""
    assert rail.CURSOR in rail.option("a", "", picked=True).plain
    assert rail.CURSOR not in rail.option("a", "", picked=False).plain


def test_hints_line_up_in_a_column() -> None:
    short = rail.option("Open", "hint", picked=False)
    long = rail.option("Change settings", "hint", picked=False)
    assert short.plain.index("hint") == long.plain.index("hint")


# -- the menu ----------------------------------------------------------------


def a_menu() -> Menu:
    return Menu(
        "What would you like to do?",
        (
            Option("open", "Open Flexi"),
            Option("settings", "Change settings"),
            Option("reset", "Start again", grave=True),
        ),
    )


def test_it_starts_on_the_safe_option() -> None:
    """The destructive row is never the one under the cursor on arrival."""
    assert a_menu().picked.value == "open"
    assert not a_menu().picked.grave


def test_arrows_move_the_cursor() -> None:
    assert a_menu().press(Key.DOWN).picked.value == "settings"
    assert a_menu().press(Key.DOWN).press(Key.DOWN).picked.value == "reset"


def test_it_wraps_at_both_ends() -> None:
    assert a_menu().press(Key.UP).picked.value == "reset"
    walked = a_menu().press(Key.DOWN).press(Key.DOWN).press(Key.DOWN)
    assert walked.picked.value == "open"


def test_a_key_that_means_nothing_here_changes_nothing() -> None:
    menu = a_menu()
    assert menu.press(Key.UNKNOWN) == menu


def test_pressing_a_key_returns_a_new_menu() -> None:
    """Immutable, so drawing is a function of the value rather than of history."""
    menu = a_menu()
    assert menu.press(Key.DOWN) is not menu
    assert menu.cursor == 0


def test_every_option_is_drawn() -> None:
    drawn = "\n".join(line.plain for line in a_menu().render())
    for label in ("Open Flexi", "Change settings", "Start again"):
        assert label in drawn
    assert "↑↓ move" in drawn


def test_a_menu_with_nothing_on_it_is_a_bug() -> None:
    with pytest.raises(ValueError, match="at least one option"):
        Menu("nothing", ())


# -- the rest of the rail ----------------------------------------------------


def test_the_wordmark_names_the_product_once_and_wears_the_accent() -> None:
    """The only line of the flow that is branding rather than content."""
    mark = rail.wordmark()

    assert "flexi" in mark.plain
    assert styles_on(mark, "flexi") == [f"bold {rail.Tone.LIVE.style}"]


def test_counts_line_up_in_a_column_however_big_they_are() -> None:
    """An inventory reads as one thing only if its figures line up.

    `overview` lists what a reset would take, and a ragged column of figures is
    read as five unrelated numbers rather than as one list.
    """
    one = rail.measure(7, "work sessions")
    many = rail.measure(1204, "clock events")

    assert one.plain.index("7") == many.plain.index("1204") + len("1204") - 1
    assert one.plain.index("work sessions") == many.plain.index("clock events")


def test_the_tail_closes_the_rail_with_or_without_a_hint() -> None:
    """It is drawn both ways.

    Under a menu the tail carries the keys that work there, and after an
    abandoned step there is nothing left to say.
    """
    assert rail.tail().plain.strip() == rail.TAIL
    assert rail.tail("esc cancel").plain.endswith("esc cancel")


# -- the terminal ------------------------------------------------------------


def paper(width: int = 60, *, terminal: bool = False) -> tuple[Console, io.StringIO]:
    """A console that writes to a string, so a prompt can be read back.

    `force_terminal` is for the two escape sequences Rich withholds from a
    plain file -- hiding and showing the cursor -- and is off otherwise so the
    text can be asserted on without colour in the way.
    """
    stream = io.StringIO()
    return Console(file=stream, width=width, force_terminal=terminal), stream


def visible(stream: io.StringIO) -> str:
    """What a person is left looking at: everything after the last rewind.

    The stream holds the whole session, including every frame of a menu being
    arrowed through. A terminal does not; it holds the last frame. This is the
    difference, and most of what is worth asserting about a prompt is in it.
    """
    return stream.getvalue().rsplit("\x1b[0J", 1)[-1]


class _Tty:
    """A stream that is, or is not, somebody's terminal."""

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
def test_a_question_is_only_asked_with_a_terminal_and_a_person_at_it(
    monkeypatch: pytest.MonkeyPatch,
    stdin_is_a_tty: bool,
    stderr_is_a_tty: bool,
    expected: bool,
) -> None:
    """Both ends are checked, because either alone is a hang.

    A pipe on stdin has a terminal to draw on and nobody to read it; a redirect
    on stderr has somebody reading and nothing to draw on. `flexi init` refuses
    to erase anything when this is false, so a script cannot answer for a
    person who is not there.
    """
    monkeypatch.setattr(sys, "stdin", _Tty(tty=stdin_is_a_tty))
    monkeypatch.setattr(sys, "stderr", _Tty(tty=stderr_is_a_tty))

    assert prompt.interactive() is expected


def test_a_prompt_is_written_to_stderr_so_a_redirect_cannot_swallow_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A prompt is not the program's output.

    `flexi init > setup.log` must not send the question into the file and leave
    somebody sitting in front of a blank terminal being waited on. The check for
    "is anybody there" reads stderr, so the writes have to go to the same
    stream or the guard is decorative.
    """
    prompt.console().print("Already set up")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Already set up" in captured.err


def test_a_question_containing_brackets_is_shown_as_it_was_written(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Rich markup is off.

    The last question Flexi asks before deleting anything is `Type 'reset' to
    continue`, and `reset` is also the name of a Rich style: with markup on, the
    word somebody has to type would be eaten out of the line telling them to
    type it.
    """
    prompt.console().print("Type [reset] to continue")

    assert "Type [reset] to continue" in capsys.readouterr().err


def test_a_line_too_long_for_the_terminal_is_cropped_rather_than_wrapped() -> None:
    """Rewinding is line arithmetic.

    A line that wrapped would be two lines to the terminal and one to the
    surface, so the next redraw would eat the line above it -- on an 80-column
    terminal, which is not an unusual one.
    """
    console, stream = paper(width=20)

    prompt.Surface(console).draw([Text("a database path that is far too long")])

    drawn = stream.getvalue().splitlines()
    assert len(drawn) == 1
    assert len(drawn[0]) <= 20


def test_a_redraw_takes_back_exactly_the_lines_it_drew() -> None:
    """Not a fixed number, and not the whole screen.

    The menu is seven lines and the step it collapses to is two.
    """
    console, stream = paper()
    surface = prompt.Surface(console)

    surface.draw([Text("one"), Text("two"), Text("three")])
    surface.redraw([Text("only this")])

    assert "\x1b[3F\x1b[0J" in stream.getvalue()
    assert visible(stream).strip() == "only this"


def test_a_surface_that_has_drawn_nothing_takes_nothing_back() -> None:
    """The line above the first thing Flexi draws belongs to the shell."""
    console, stream = paper()

    prompt.Surface(console).rewind()

    assert stream.getvalue() == ""


def test_a_line_left_open_keeps_the_cursor_on_it() -> None:
    """The confirmation is typed into, not tapped at.

    The answer has to appear beside the prompt rather than on the line under it.
    """
    console, stream = paper()

    prompt.Surface(console).draw_open(Text("› "))

    assert not stream.getvalue().endswith("\n")


def test_the_cursor_comes_back_even_when_the_answer_never_does() -> None:
    """A menu hides the cursor.

    Leaving it hidden because somebody pressed ctrl-c leaves them typing blind
    into their own shell afterwards, and the only cure is a `reset` they have to
    know to run.
    """
    console, stream = paper(terminal=True)

    surface = prompt.Surface(console)
    msg = "the terminal went away"

    with pytest.raises(RuntimeError, match=msg), surface.without_cursor():
        raise RuntimeError(msg)

    assert stream.getvalue().endswith("\x1b[?25h")


def test_abandoning_closes_the_rail_and_says_why() -> None:
    """There is no half-open rail.

    Every path out of `flexi init` ends with the line drawn to its end, so a
    transcript reads as a finished thing.
    """
    console, stream = paper()

    prompt.abandon("Nothing was changed", console)

    assert rail.TAIL in visible(stream)
    assert "Nothing was changed" in visible(stream)


# -- reading keys off a Windows console --------------------------------------
#
# The POSIX reader is tested against a real pty in `test_terminal.py`, because
# there the mode is the thing that can be wrong. These run everywhere: on
# Windows there is no mode, only the two-step scan-code protocol below, and a
# function that returns characters exercises it exactly as `msvcrt` would.


def typing(*characters: str) -> Callable[[], str]:
    """Somebody at a Windows keyboard, one `getwch` call at a time."""
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
def test_the_windows_console_vocabulary(
    characters: tuple[str, ...], expected: Key
) -> None:
    r"""Both prefixes are read.

    `\x00` and `\xe0` mean the same thing -- a scan code follows -- and which
    one arrives depends on the key and the keyboard. Reading one and not the
    other makes the arrows work on some machines.
    """
    assert prompt._read_windows(typing(*characters)) is expected


def test_a_windows_scan_code_flexi_has_no_use_for_is_not_a_key() -> None:
    """F1 is two reads, and swallowing only the first desynchronises the loop.

    The prefix has to be followed by its code whatever the code turns out to
    be, or the next press is read as the tail of this one and the menu jumps.
    """
    keyboard = typing("\x00", ";", "j")

    assert prompt._read_windows(keyboard) is Key.UNKNOWN
    assert prompt._read_windows(keyboard) is Key.DOWN


def test_escape_on_windows_is_not_the_start_of_anything() -> None:
    """The POSIX reader waits to find out; this one cannot need to.

    An arrow never begins with escape on a Windows console, so a lone escape is
    answerable on the first read -- and the wait that makes the POSIX reader
    correct would only be a delay here.
    """
    keyboard = typing("\x1b", "\x00", "H")

    assert prompt._read_windows(keyboard) is Key.QUIT
    assert prompt._read_windows(keyboard) is Key.UP


# -- choosing ----------------------------------------------------------------


@contextmanager
def _no_terminal() -> Iterator[int]:
    """Stands in for cbreak mode, which the pty tests above cover for real."""
    yield -1


def options() -> Sequence[Option]:
    return (
        Option("open", "Open Flexi", "your records, as they are"),
        Option("settings", "Change settings", "leave year, hours, region"),
        Option("reset", "Start again", "erase 12 records", grave=True),
    )


@pytest.fixture
def pressing(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Somebody at the keyboard, pressing the keys they are given in order."""
    monkeypatch.setattr(prompt, "_unbuffered", _no_terminal)

    def press(*keys: Key) -> None:
        presses = iter(keys)

        def read(_descriptor: int) -> Key:
            return next(presses)

        monkeypatch.setattr(prompt, "read_key", read)

    return press


def test_arrowing_down_and_pressing_enter_returns_the_row_landed_on(
    pressing: Callable[..., None],
) -> None:
    console, _ = paper()
    pressing(Key.DOWN, Key.ENTER)

    picked = prompt.choose("What would you like to do?", options(), out=console)

    assert picked is not None
    assert picked.value == "settings"


def test_the_answered_step_collapses_to_the_question_and_the_answer(
    pressing: Callable[..., None],
) -> None:
    """A transcript should read as a record of what was chosen.

    Not as the wreckage of a menu that has been arrowed through, which is what
    every frame of it left on screen would amount to.
    """
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
    """`ask` turns `None` into "leave everything alone".

    A menu that returned its first option on escape would open Flexi at somebody
    who was trying to get out of it.
    """
    console, stream = paper()
    pressing(key)

    assert prompt.choose("What would you like to do?", options(), out=console) is None
    assert "Nothing chosen" in visible(stream)
    assert "Start again" not in visible(stream)


def test_a_ctrl_c_the_terminal_turns_into_a_signal_is_still_a_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ctrl-c arrives two ways, and both of them are a refusal.

    Cbreak leaves signal handling on deliberately, so ctrl-c can arrive as a
    `KeyboardInterrupt` raised out of the read rather than as the byte the
    reader was expecting. It means the same thing, and it must not surface as a
    traceback across a menu with the cursor still hidden.
    """
    console, stream = paper()

    def interrupted(_descriptor: int) -> Key:
        raise KeyboardInterrupt

    monkeypatch.setattr(prompt, "_unbuffered", _no_terminal)
    monkeypatch.setattr(prompt, "read_key", interrupted)

    assert prompt.choose("What would you like to do?", options(), out=console) is None
    assert "Nothing chosen" in visible(stream)


def test_a_key_that_means_nothing_here_leaves_the_cursor_where_it_was(
    pressing: Callable[..., None],
) -> None:
    console, _ = paper()
    pressing(Key.UNKNOWN, Key.ENTER)

    picked = prompt.choose("What would you like to do?", options(), out=console)

    assert picked is not None
    assert picked.value == "open"


# -- typing the word ---------------------------------------------------------


@pytest.mark.parametrize(
    "typed",
    [
        pytest.param("reset\n", id="as asked"),
        pytest.param("  reset  \n", id="with the spaces a paste brings"),
        pytest.param("RESET\n", id="shouted"),
    ],
)
def test_the_word_spelled_out_is_what_opens_the_gate(
    monkeypatch: pytest.MonkeyPatch, typed: str
) -> None:
    """A keystroke can be muscle memory; spelling a word out cannot.

    This is the last gate before Flexi deletes anything.
    """
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
    """Including the empty line.

    That is what `yes '' | flexi init` sends, and what a person leaning on enter
    to dismiss a prompt sends too.
    """
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
def test_an_abandoned_confirmation_deletes_nothing(
    monkeypatch: pytest.MonkeyPatch, interruption: type[BaseException]
) -> None:
    """Getting out of the last question is a refusal, not a traceback.

    Nothing has been deleted at this point, and the answer to somebody reaching
    for ctrl-c is that nothing will be.
    """
    console, _ = paper()
    monkeypatch.setattr(sys, "stdin", _Interrupted(interruption))

    assert not prompt.type_the_word("reset", "Type 'reset' to continue", out=console)


def test_the_confirmation_closes_the_rail_whatever_the_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rail closes over whatever was typed.

    The answer is left on screen above a closed rail, so the record of what was
    agreed to survives in the scrollback.
    """
    console, stream = paper()
    monkeypatch.setattr(sys, "stdin", io.StringIO("no\n"))

    prompt.type_the_word("reset", "Type 'reset' to continue", out=console)

    assert stream.getvalue().rstrip().endswith(rail.tail().plain.rstrip())
